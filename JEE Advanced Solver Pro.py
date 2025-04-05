import streamlit as st
import google.generativeai as genai
from langgraph.graph import StateGraph
from typing import TypedDict
import time
import random
import asyncio
from mistralai import Mistral

# Ensure asyncio event loop compatibility
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# Define State Schema
class GraphState(TypedDict):
    api_key: str
    questions_text: str
    context_text: str
    generated_answers: dict

# Function to upload PDF to Mistral OCR and get signed URL
def upload_pdf_to_mistral(uploaded_file, api_key):
    client = Mistral(api_key=api_key)
    with st.spinner(f"Uploading {uploaded_file.name} to Mistral OCR..."):
        try:
            uploaded_pdf = client.files.upload(
                file={
                    "file_name": uploaded_file.name,
                    "content": uploaded_file.getvalue(),
                },
                purpose="ocr"
            )
            file_id = uploaded_pdf.id
            if not file_id:
                return None, "Error: Failed to get file_id from Mistral OCR response."
            
            signed_url = client.files.get_signed_url(file_id=file_id)
            return signed_url.url, None
        except Exception as e:
            return None, f"Error uploading PDF: {e}"

# Function to extract text from PDFs using Mistral OCR
def extract_text_from_pdf_mistral(document_url, api_key):
    if not document_url:
        return "Error: No valid document URL provided."
    
    client = Mistral(api_key=api_key)
    with st.spinner("Extracting text from PDF using Mistral OCR..."):
        try:
            ocr_response = client.ocr.process(
                model="mistral-ocr-latest",
                document={"type": "document_url", "document_url": document_url},
                include_image_base64=False
            )
            pages = ocr_response.pages if hasattr(ocr_response, "pages") else []
            extracted_text = "\n\n".join(page.markdown for page in pages)
            return extracted_text if extracted_text else "No text found."
        except Exception as e:
            return f"Error extracting text: {e}"

# LangGraph setup
graph = StateGraph(GraphState)

def call_gemini_with_retry(prompt, api_key, max_retries=3):
    """Retries Gemini API calls if resource is exhausted."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            return response.text if hasattr(response, "text") else ""
        except Exception as e:
            if "ResourceExhausted" in str(e):
                wait_time = 2 ** attempt + random.uniform(0, 1)
                time.sleep(wait_time)
            else:
                return f"Error: {str(e)}"
    return "Error: API quota exceeded. Please try again later."

# Function to analyze context relevance to questions
def analyze_context_relevance(state: GraphState) -> GraphState:
    """Pre-analyzes context material to identify relevant information for each question."""
    if not state["context_text"] or "Error:" in state["context_text"] or "No text found" in state["context_text"]:
        state["relevance_analysis"] = {}
        return state
    
    prompt = """
    You are an expert at analyzing educational materials for JEE Advanced. Your task is to identify what information in the context,concepts material is relevant to each of the questions.
    
    CONTEXT MATERIAL:
    {}
    
    QUESTIONS TEXT:
    {}
    
    For each question you identify in the QUESTIONS TEXT:
    1. Extract the question number and core topic/concept of the question
    2. Search the context material for ANY of the following that could be relevant:
       - Similar solved examples or problems
       - Explanations of the mathematical/scientific concepts involved
       - Formulas, theorems, or principles that apply to this question
       - Methods or techniques mentioned that could help solve this type of problem
       - Any diagrams, figures, or illustrations related to the topic
    
    Provide your analysis in this format:
    
    Question [X]: [Core topic/concept]
    Relevant Information:
    - [Specific relevant information found in context material and explained step by step]
    - [Another piece of relevant information and explained step by step]
    Connection: [Explain how this information helps solve the question step by step]
    
    Note: Consider even indirect connections where concepts or techniques might be adapted from one scenario to another which will help to solve the question.
    """.format(state["context_text"], state["questions_text"])
    
    relevance_analysis = call_gemini_with_retry(prompt, state["api_key"])
    
    state["relevance_analysis"] = relevance_analysis
    return state

# Agent: Solve Questions with Context
def solve_questions(state: GraphState) -> GraphState:
    # Determine if context is available
    has_context = state["context_text"] and "Error:" not in state["context_text"] and "No text found" not in state["context_text"]
    
    # Get pre-analyzed context relevance if it exists
    relevance_info = state.get("relevance_analysis", "")
    
    if has_context:
        prompt = """
        You are an expert at solving JEE Advanced level problems. Your task is to extract questions from the questions PDF, identify relevant information from the context PDF, and provide detailed solutions.
        followings that could be relevant information:
       - Similar solved examples or problems
       - Explanations of the mathematical/scientific concepts involved
       - Formulas, theorems, or principles that apply to this question
       - Methods or techniques mentioned that could help solve this type of problem
       - Any diagrams, figures, or illustrations related to the topic
       - Consider even indirect connections where concepts or techniques might be adapted from one scenario to another which will help to solve the question

        CONTEXT MATERIAL:
        {}
        
        QUESTIONS TEXT:
        {}
        
        RELEVANCE ANALYSIS:
        {}
        
        FORMAT INSTRUCTIONS:
        For each question you identify in the QUESTIONS TEXT:
        
        1. First, extract and present the EXACT question as it appears in the original text, preserving all mathematical notation, question numbers, and formatting.
           Format: "**Question X:** [exact question text as it appears in the PDF]" (replace X with the actual question number)
        
        2. MANDATORY: Provide a detailed analysis of how the context material helps with this question:
           Format: "**Context Analysis:**"
           - If you found DIRECTLY relevant information (like a similar solved problem), explain: "The context PDF contains a directly relevant example/explanation about [specific concept] which shows how to [approach/technique] and explain how it is relevant step by step."
           - If you found INDIRECTLY relevant information (like related concepts that can be adapted), explain: "The context PDF discusses [related concept/principle] which can be adapted to this problem because [explanation of connection]. and explain step by step how it can be adapted to this problem step by step."
           - If you found CONCEPTUAL information (like formulas or principles), explain: "The context PDF provides key formulas/principles for [topic] which apply to this problem, specifically [mention specific formula/principle] and explain this CONCEPTUAL information can be used to solve the problem step by step."
           - If NO relevant information was found, state: "The context PDF does not contain information applicable to this problem. I will solve it using my standard JEE Advanced knowledge."
        
        3. Provide a complete, step-by-step solution with:
           - Clear identification of the mathematical principles and formulas being applied
           - Where relevant, explicitly mention how you're applying information from the context PDF
           - Every step of calculation shown explicitly
           - Explanation of the reasoning at each step
           - Format mathematical expressions clearly using proper notation
        
        4. End with the final answer clearly marked as "**Answer:** [final result]"
        
        5. Separate each question with a horizontal line (---) for clarity
        
        IMPORTANT: 
        - You MUST preserve and present the exact question text as it appears in the PDF before attempting to solve it.
        - Be specific about how context information is being used in your solution rather than making general claims.
        - Relevant information doesn't just mean identical problems - it includes related concepts, applicable formulas, similar problem-solving techniques, or explanations that clarify the underlying principles.
        """.format(state["context_text"], state["questions_text"], relevance_info)
    else:
        prompt = """
        You are an expert at solving JEE Advanced level problems. Your task is to extract questions from the questions PDF and provide detailed solutions.
        
        QUESTIONS TEXT:
        {}
        
        FORMAT INSTRUCTIONS:
        For each question you identify in the QUESTIONS TEXT:
        
        1. First, extract and present the EXACT question as it appears in the original text, preserving all mathematical notation, question numbers, and formatting.
           Format: "**Question X:** [exact question text as it appears in the PDF]" (replace X with the actual question number)
        
        2. MANDATORY: State: "**Analysis:** Solving this question using standard JEE Advanced knowledge on [identify the specific topic/concept]."
        
        3. Provide a complete, step-by-step solution with:
           - Clear identification of the mathematical principles and formulas being applied
           - Every step of calculation shown explicitly
           - Explanation of the reasoning at each step
           - Format mathematical expressions clearly using proper notation
        
        4. End with the final answer clearly marked as "**Answer:** [final result]"
        
        5. Separate each question with a horizontal line (---) for clarity
        
        IMPORTANT: You MUST preserve and present the exact question text as it appears in the PDF before attempting to solve it. Do not paraphrase or summarize the questions.
        """.format(state["questions_text"])
    
    solved_answers = call_gemini_with_retry(prompt, state["api_key"])
    
    state["generated_answers"] = {
        "Solutions": solved_answers,
        "Context_Used": has_context,
        "Relevance_Analysis": relevance_info if has_context else ""
    }
    
    with st.empty():
        st.subheader("AI Analysis & Solutions")
        if has_context:
            st.info("Solutions generated with context material where applicable. The AI has analyzed the context PDF for relevant information and will explain how it uses this information for each question.")
        else:
            st.info("Solutions generated using AI knowledge (no context material provided).")
        st.success(solved_answers)
    
    return state

# Add nodes to graph
graph.add_node("analyze_context", analyze_context_relevance)
graph.add_node("solve_questions", solve_questions)

# Add edges
if "analyze_context" in graph.nodes and "solve_questions" in graph.nodes:
    graph.add_edge("analyze_context", "solve_questions")
    graph.set_entry_point("analyze_context")
else:
    graph.set_entry_point("solve_questions")

executor = graph.compile()

# Streamlit UI
st.set_page_config(page_title="JEE Advanced Solver Pro", layout="wide")
st.title("📘 JEE Advanced Solver Pro")
st.markdown("""
This app extracts and solves JEE Advanced questions with precision and clarity.
* Upload a **Questions PDF** (required) - Contains the JEE problems to solve
* Upload a **Context PDF** (optional) - Contains reference material, concepts, or solved examples
* The AI will analyze the context PDF to find relevant information for each question and explain how it applies
""")

# Create two columns for API keys
col1, col2 = st.columns(2)
with col1:
    api_key = st.text_input("🔑 Enter your Gemini API Key", type="password")
with col2:
    mistral_api_key = st.text_input("🔑 Enter your Mistral API Key", type="password")

# Create two columns for file uploaders
col1, col2 = st.columns(2)
with col1:
    questions_pdf = st.file_uploader("📂 Upload Questions PDF (Required)", type=["pdf"])
with col2:
    context_pdf = st.file_uploader("📂 Upload Context PDF (Optional)", type=["pdf"], 
                                  help="PDF containing study material, concepts, formulas, solved examples, or any relevant information")

if api_key and mistral_api_key and questions_pdf:
    if st.button("🚀 Extract & Solve Questions"):
        try:
            with st.expander("Processing Status", expanded=True):
                # Process Questions PDF
                st.subheader("Step 1: Processing Questions PDF")
                questions_url, questions_upload_error = upload_pdf_to_mistral(questions_pdf, mistral_api_key)
                if questions_upload_error:
                    st.error(questions_upload_error)
                    st.stop()
                
                questions_text = extract_text_from_pdf_mistral(questions_url, mistral_api_key)
                if "No text found" in questions_text:
                    st.error("Failed to extract text from the questions PDF. Please try a different PDF or check if it contains readable text.")
                    st.stop()
                st.success("✅ Questions Extracted Successfully!")
                
                # Process Context PDF if provided
                context_text = ""
                if context_pdf:
                    st.subheader("Step 2: Processing Context PDF")
                    context_url, context_upload_error = upload_pdf_to_mistral(context_pdf, mistral_api_key)
                    if context_upload_error:
                        st.warning(f"Warning with context PDF: {context_upload_error}")
                        st.warning("Proceeding without context information.")
                    else:
                        context_text = extract_text_from_pdf_mistral(context_url, mistral_api_key)
                        if "No text found" in context_text:
                            st.warning("No readable text found in the context PDF. Proceeding with AI knowledge only.")
                        else:
                            st.success("✅ Context Material Extracted Successfully!")
                            st.info("⏳ Analyzing context material for relevance to questions...")
                else:
                    st.info("No context PDF provided. Will solve questions using AI knowledge only.")
                
                # Run AI Agent
                step_num = 3 if context_pdf else 2
                st.subheader(f"Step {step_num}: Running AI Agent to Analyze & Solve ⚙️")
                
                with st.spinner("Processing questions and generating solutions..."):
                    state = executor.invoke({
                        "api_key": api_key,
                        "questions_text": questions_text,
                        "context_text": context_text,
                        "generated_answers": {}
                    })
                
                st.success("🎉 Process Completed Successfully! Solutions are ready below.")
            
            # Display results outside the expander
            st.subheader("Solution Results")
            if context_pdf and "No text found" not in context_text and "Error:" not in context_text:
                st.info("""
                🔍 **Enhanced Context Analysis:** For each question, the AI has:
                - Identified specific concept connections between questions and context material
                - Explained how relevant information from the context is applied in the solution
                - Distinguished between direct examples, related concepts, and fundamental principles
                """)
            else:
                st.info("🧠 Solutions were generated using AI knowledge since no context material was provided or processed.")
            
            # Add download buttons for solutions
            solution_text = state["generated_answers"].get("Solutions", "")
            col1, col2 = st.columns(2)
            with col1:
                st.download_button(
                    label="📥 Download Solutions as Text",
                    data=solution_text,
                    file_name="jee_solutions.txt",
                    mime="text/plain"
                )
            
            # Create tabs for viewing solutions and analysis
            if context_pdf and "No text found" not in context_text and "Error:" not in context_text:
                tab1, tab2, tab3 = st.tabs(["Solutions", "Context Analysis", "Extracted Text"])
                
                with tab1:
                    st.markdown("### Detailed Solutions:")
                    st.markdown(solution_text)
                
                with tab2:
                    st.markdown("### Context Relevance Analysis:")
                    st.markdown(state["generated_answers"].get("Relevance_Analysis", "No relevance analysis available."))
                
                with tab3:
                    st.markdown("### Extracted Questions Text:")
                    st.text_area("Questions PDF Content", questions_text, height=250)
                    if context_pdf and context_text and "No text found" not in context_text:
                        st.markdown("### Extracted Context Text:")
                        st.text_area("Context PDF Content", context_text, height=250)
            else:
                tab1, tab2 = st.tabs(["Solutions", "Extracted Text"])
                
                with tab1:
                    st.markdown("### Detailed Solutions:")
                    st.markdown(solution_text)
                
                with tab2:
                    st.markdown("### Extracted Questions Text:")
                    st.text_area("Questions PDF Content", questions_text, height=300)
        
        except Exception as e:
            st.error(f"An error occurred: {str(e)}")
            st.error("Please check your API keys and try again.")
else:
    if not api_key or not mistral_api_key:
        st.warning("Please enter both API keys to proceed.")
    if not questions_pdf:
        st.warning("Please upload a Questions PDF to proceed.")

# Add a footer with additional information
st.markdown("---")
st.markdown("""
### How to Use This App
1. **Upload a Questions PDF** containing JEE Advanced problems
2. **Optionally upload a Context PDF** with study material, concepts, formulas, or solved examples
3. The AI will:
   - Extract text from your PDFs with high precision
   - Analyze the context material to identify relevant information for each question
   - Explain specifically how context information applies   to each question
   - Provide detailed, step-by-step solutions

### What Counts as "Relevant Information"
The AI considers information in the context PDF to be relevant if it:
- Contains similar solved problems or examples
- Explains concepts or principles needed to solve the question
- Provides formulas or theorems applicable to the problem
- Demonstrates problem-solving techniques that can be adapted
- Includes diagrams or illustrations of related concepts
- Covers topics that are indirectly connected but can be applied with modifications

The AI will explicitly explain these connections in each solution.

### Tips for Best Results
- Use clear, readable PDFs
- For context PDFs, include textbooks, lecture notes, or solved example papers
- The more comprehensive your context material, the better the AI can leverage it
""")    