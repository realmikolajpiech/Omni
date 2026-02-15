import os
import logging

# Setup logging
logger = logging.getLogger(__name__)

def read_pdf(path):
    """Extracts text from a PDF file."""
    try:
        import pypdf
        text = ""
        with open(path, 'rb') as f:
            reader = pypdf.PdfReader(f)
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
        return text
    except Exception as e:
        logger.error(f"Error reading PDF {path}: {e}")
        return ""

def read_docx(path):
    """Extracts text from a DOCX file."""
    try:
        import docx
        doc = docx.Document(path)
        text = "\n".join([para.text for para in doc.paragraphs])
        return text
    except Exception as e:
        logger.error(f"Error reading DOCX {path}: {e}")
        return ""

def strip_rtf(rtf_text):
    """Simple RTF stripper using regex."""
    import re
    # Match curly braces, control words, or escaped chars
    # This is a basic approximation but works for search indexing
    text = rtf_text
    
    # Remove groups (e.g., font tables, stylesheets) that we definitely don't want
    # This is hard with regex due to nesting, but we can try to remove common headers
    
    # 1. Remove RTF commands
    # Command: \word or \word123 (control word) or \_ (symbol)
    text = re.sub(r'\\[a-zA-Z]+(-?[0-9]*) ?', ' ', text)
    
    # 2. Remove braces
    text = text.replace('{', '').replace('}', '')
    
    # 3. Fix special characters if possible, but for search, spaces are fine
    text = re.sub(r'\\', ' ', text)
    
    # 4. Clean up whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

def process_file_content(path, chunk_size=1000, overlap=100):
    """Reads file content and returns chunks."""
    try:
        # Skip large files (> 5MB) for now to avoid freezing
        # Increased limit for PDFs/DOCX as they can be larger but text content is smaller
        if os.path.getsize(path) > 5 * 1024 * 1024:
            return []

        _, ext = os.path.splitext(path)
        ext = ext.lower()
        
        content = ""
        
        if ext == '.pdf':
            content = read_pdf(path)
        elif ext == '.docx':
            content = read_docx(path)
        elif ext == '.rtf':
             try:
                 with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    raw = f.read()
                    content = strip_rtf(raw)
             except:
                 content = ""
        else:
            # Default text reading
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        
        if not content:
            return []

        chunks = []
        for i in range(0, len(content), chunk_size - overlap):
            chunk = content[i:i + chunk_size]
            if len(chunk) < 50: continue # Skip tiny chunks
            chunks.append(chunk)
        return chunks
    except Exception as e:
        logger.error(f"Error processing file {path}: {e}")
        return []

TEXT_EXTENSIONS = {
    '.txt', '.md', '.py', '.js', '.html', '.css', '.c', '.cpp', '.h', '.sh', 
    '.json', '.yml', '.yaml', '.toml', '.ini', '.cfg', '.conf', '.java', '.rs', '.go',
    '.pdf', '.docx', '.rtf'
}

def is_text_file(path):
    _, ext = os.path.splitext(path)
    return ext.lower() in TEXT_EXTENSIONS
