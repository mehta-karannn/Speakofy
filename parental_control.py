import streamlit as st
import sqlite3
import datetime
import os
from PyPDF2 import PdfReader
import google.generativeai as genai
from dotenv import load_dotenv

# ---------------- ENV SETUP ----------------
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # set in .env: GEMINI_API_KEY=your_key
if not GEMINI_API_KEY:
    st.warning("GEMINI_API_KEY not found. Create a .env file with GEMINI_API_KEY=your_key")
genai.configure(api_key=GEMINI_API_KEY)

# ---------------- DATABASE SETUP ----------------
conn = sqlite3.connect("users.db", check_same_thread=False)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    email TEXT UNIQUE,
    password TEXT,
    dob TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    filename TEXT,
    content TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id)
)
""")
conn.commit()

# ---------------- HELPER FUNCTIONS ----------------
def add_user(name, email, password, dob):
    c.execute("INSERT INTO users (name, email, password, dob) VALUES (?, ?, ?, ?)", (name, email, password, dob))
    conn.commit()

def get_user(email, password):
    c.execute("SELECT * FROM users WHERE email = ? AND password = ?", (email, password))
    return c.fetchone()

def user_exists(email):
    c.execute("SELECT 1 FROM users WHERE email = ?", (email,))
    return c.fetchone()

def calculate_age(dob):
    today = datetime.date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

def save_file(user_id, filename, content):
    c.execute("INSERT INTO files (user_id, filename, content) VALUES (?, ?, ?)", (user_id, filename, content))
    conn.commit()

def get_user_last_file_content(user_id):
    c.execute("SELECT content FROM files WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
    row = c.fetchone()
    return row[0] if row else None

def get_all_files():
    """Return list of (id, filename, user_id, user_name) for all uploaded files."""
    c.execute("""
        SELECT f.id, f.filename, u.id as user_id, u.name
        FROM files f
        LEFT JOIN users u ON u.id = f.user_id
        ORDER BY f.id DESC
    """)
    return c.fetchall()

def get_file_content_by_id(file_id):
    c.execute("SELECT content FROM files WHERE id = ?", (file_id,))
    row = c.fetchone()
    return row[0] if row else None

# ---------------- PDF PROCESSING ----------------
def extract_pdf_text(file):
    pdf_reader = PdfReader(file)
    text = ""
    for page in pdf_reader.pages:
        pg = page.extract_text()
        if pg:
            text += pg + "\n"
    return text.strip()

# ---------------- STREAMLIT STATE ----------------
st.set_page_config(page_title="Speakofy", layout="wide")
st.sidebar.title("Speakofy Navigation")
page = st.sidebar.radio("Go to", ["Parental Control", "Q&A"])

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "name" not in st.session_state:
    st.session_state.name = ""
if "book_content" not in st.session_state:
    st.session_state.book_content = None  # cache last-loaded/last-uploaded book text

# ---------------- PAGE 1: LOGIN / SIGNUP + UPLOAD ----------------
if page == "Parental Control":
    st.title("Speakofy - Parental Control")

    if not st.session_state.logged_in:
        tab1, tab2 = st.tabs(["Login", "Signup"])

        with tab1:
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            if st.button("Login", key="login_btn"):
                user = get_user(email, password)
                if user:
                    st.session_state.logged_in = True
                    st.session_state.user_id = user[0]
                    st.session_state.name = user[1]
                    # Preload user's last book into session (if any)
                    st.session_state.book_content = get_user_last_file_content(user[0])
                    st.success(f"Welcome {user[1]}!")
                else:
                    st.error("Invalid credentials")

        with tab2:
            name = st.text_input("Name", key="signup_name")
            email_s = st.text_input("Email", key="signup_email")
            password_s = st.text_input("Password", type="password", key="signup_password")
            confirm_password = st.text_input("Confirm Password", type="password", key="signup_confirm_password")
            dob = st.date_input("Date of Birth", min_value=datetime.date(1900, 1, 1), key="signup_dob")
            if st.button("Signup", key="signup_btn"):
                age = calculate_age(dob)
                if age < 25:
                    st.error("You must be 25+ years old.")
                elif password_s != confirm_password:
                    st.error("Passwords do not match.")
                elif user_exists(email_s):
                    st.error("Email already registered.")
                else:
                    add_user(name, email_s, password_s, str(dob))
                    st.success("Registration successful! Please log in.")

    else:
        st.subheader(f"Hello {st.session_state.name} 👋")
        uploaded_file = st.file_uploader("Choose a PDF", type=["pdf"], key="pdf_upload")
        if uploaded_file and st.button("Upload PDF", key="upload_btn"):
            text = extract_pdf_text(uploaded_file)
            if not text:
                st.error("Couldn't extract text from this PDF. Try another file.")
            else:
                save_file(st.session_state.user_id, uploaded_file.name, text)
                # Cache the uploaded content in session so Q&A page sees it immediately
                st.session_state.book_content = text
                st.success(f"{uploaded_file.name} uploaded and learned successfully!")

# ---------------- PAGE 2: Q&A ----------------
elif page == "Q&A":
    st.title("Speakofy - Ask Questions")

    if not st.session_state.logged_in:
        st.warning("Please log in first from 'Parental Control' page.")
    else:
        # Cross-user: allow querying ANY uploaded book
        all_files = get_all_files()
        if not all_files:
            st.warning("No books uploaded yet.")
        else:
            # Build display names like "filename.pdf (by Alice)"
            options = {f"{fname} (by {uname if uname else 'Unknown'})": fid for (fid, fname, uid, uname) in all_files}
            selected_label = st.selectbox("Choose a book to query", list(options.keys()), key="select_book")
            selected_file_id = options[selected_label]

            # Load selected book content (and cache)
            content = get_file_content_by_id(selected_file_id)
            if content:
                st.session_state.book_content = content

            if not content:
                st.warning("Could not load the selected book. Try another one or upload a new PDF.")
            else:
                question = st.text_input("Ask a question about the selected book", key="qa_question")
                if st.button("Get Answer", key="qa_btn"):
                    model = genai.GenerativeModel("gemini-1.5-flash")
                    prompt = (
                        "You are a helpful tutor. Answer ONLY using the following book content. "
                        "If the answer isn't present, say you don't know.\n\n"
                        f"BOOK CONTENT:\n{content}\n\n"
                        f"QUESTION:\n{question}\n\n"
                        "ANSWER:"
                    )
                    try:
                        response = model.generate_content(prompt)
                        st.write("**Answer:**", response.text)
                    except Exception as e:
                        st.error(f"Error: {e}")
