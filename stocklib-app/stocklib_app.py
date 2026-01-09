import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import os
import zipfile
import base64
import time
import urllib.parse
import io
import random
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import tempfile

# --- Page Configuration ---
st.set_page_config(
    page_title="StockLib",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# --- CSS: Monochrome Dark Theme (Earning Calls Analyzer Style) ---
st.markdown("""
<style>
    /* Global Reset & Fonts */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    /* Backgrounds */
    .stApp {
        background-color: #050505; /* Deep black/gray */
    }

    /* Input Fields */
    .stTextInput > div > div > input {
        background-color: #1a1a1a;
        color: #ffffff;
        border: 1px solid #333333;
        border-radius: 6px;
        padding: 10px 12px;
    }
    .stTextInput > div > div > input:focus {
        border-color: #555555;
        box-shadow: none;
    }

    /* Headers */
    h1, h2, h3 {
        color: white !important;
        font-weight: 600;
        text-align: center;
    }

    /* Scrollable Box Container */
    .scroll-box-container {
        background-color: #111111;
        border: 1px solid #333333;
        border-radius: 8px;
        padding: 1rem;
        height: 600px; /* Fixed height for scroll */
        overflow-y: auto; 
    }
    
    .category-header {
        font-size: 1.1rem;
        font-weight: 600;
        color: #e5e5e5;
        margin-bottom: 1rem;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 1px solid #333333;
        padding-bottom: 0.5rem;
    }

    /* Checkboxes */
    .stCheckbox {
        color: #d1d5db; /* Gray-300 */
    }
    /* Streamlit Checkbox Tick Color Override (Best effort via CSS hack) */
    iframe {
       color-scheme: dark;
    }

    /* Download Button (Centered, Big) */
    .stDownloadButton button {
        background-color: white !important;
        color: black !important;
        border: none;
        font-weight: 600;
        padding: 0.75rem 2rem;
        border-radius: 6px;
        width: 100%;
        margin-top: 2rem;
    }
    .stDownloadButton button:hover {
        background-color: #e5e5e5 !important;
    }
    
    /* Search Button to look like standard button */
    .stButton button {
        background-color: #333;
        color: white;
        border: 1px solid #444;
    }

    /* Hide Streamlit brand */
    #MainMenu, footer, header {visibility: hidden;}
    
</style>
""", unsafe_allow_html=True)

# --- Global Constants & Config ---
MIN_FILE_SIZE = 1024
REQUESTS_CONNECT_TIMEOUT = 15
REQUESTS_READ_TIMEOUT = 300
SELENIUM_PAGE_LOAD_TIMEOUT = 300
USER_AGENTS = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36"]

# --- Core Backend Functions (Unchanged Logic, just helper functions) ---

def get_extension_from_response(response, url, doc_type_for_default):
    content_disposition = response.headers.get('Content-Disposition')
    if content_disposition:
        filenames = re.findall(r'filename\*?=(?:UTF-\d{1,2}\'\'|")?([^";\s]+)', content_disposition, re.IGNORECASE)
        if filenames:
            parsed_filename = urllib.parse.unquote(filenames[-1].strip('"'))
            _, ext = os.path.splitext(parsed_filename)
            if ext and 1 < len(ext) < 7: return ext.lower()
    content_type = response.headers.get('Content-Type')
    if content_type:
        ct = content_type.split(';')[0].strip().lower()
        mime_to_ext = {'application/pdf': '.pdf', 'application/zip': '.zip', 'text/csv': '.csv'}
        if ct in mime_to_ext: return mime_to_ext[ct]
    return '.pdf'

def format_filename_base(date_str, doc_type):
    if re.match(r'^\d{4}$', date_str): return f"{date_str}_{doc_type}"
    if re.match(r'^\d{4}-\d{2}$', date_str): return f"{date_str}_{doc_type}"
    clean_date = re.sub(r'[^\w\.-]', '_', date_str)
    return f"{clean_date}_{doc_type}"

def get_webpage_content(stock_name):
    url = f"https://www.screener.in/company/{stock_name}/consolidated/#documents"
    try:
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        response = requests.get(url, headers=headers, timeout=REQUESTS_CONNECT_TIMEOUT)
        response.raise_for_status()
        return response.text
    except Exception as e:
        return {"error": str(e)}

def parse_html_content(html_content):
    if not html_content: return []
    soup = BeautifulSoup(html_content, 'html.parser')
    all_links = []
    
    # Annual Reports
    for link in soup.select('.annual-reports ul.list-links li a'):
        if (year_match := re.search(r'Financial Year (\d{4})', link.text.strip())):
            all_links.append({'date': year_match.group(1), 'type': 'Annual_Report', 'url': link['href'], 'display_name': f"FY {year_match.group(1)}"})
            
    # Concalls & PPTs
    for item in soup.select('.concalls ul.list-links li'):
        if (date_div := item.select_one('.ink-600.font-size-15')):
            date_text = date_div.text.strip()
            # Clean date for display
            try: date_display = datetime.strptime(date_text, '%b %Y').strftime('%b %Y')
            except ValueError: date_display = date_text
            
            try: date_sort = datetime.strptime(date_text, '%b %Y').strftime('%Y-%m')
            except ValueError: date_sort = date_text
            
            for link_tag in item.find_all('a', class_='concall-link'):
                if 'Transcript' in link_tag.text:
                    all_links.append({'date': date_sort, 'type': 'Transcript', 'url': link_tag['href'], 'display_name': f"{date_display} Transcript"})
                elif 'PPT' in link_tag.text:
                    all_links.append({'date': date_sort, 'type': 'PPT', 'url': link_tag['href'], 'display_name': f"{date_display} Presentation"})

    # Credit Ratings
    for link in soup.select('.credit-ratings ul.list-links li a'):
         date_elem = link.select_one('.ink-600')
         date_str = date_elem.text.strip() if date_elem else ""
         clean_text = link.text.strip()
         title = clean_text.replace(date_str, '').strip()
         
         display_name = f"{title} ({date_str})" if date_str else title
         if len(display_name) > 60: display_name = display_name[:57] + "..."
         
         all_links.append({'date': '9999', 'type': 'Credit_Rating', 'url': link['href'], 'display_name': display_name})


    return sorted(all_links, key=lambda x: x['date'], reverse=True)

# --- Session State Management ---

if 'search_performed' not in st.session_state:
    st.session_state.search_performed = False
if 'documents' not in st.session_state:
    st.session_state.documents = {'Annual_Report': [], 'Transcript': [], 'PPT': [], 'Credit_Rating': []}
if 'selections' not in st.session_state:
    st.session_state.selections = {} # Key: url, Value: bool

# --- Helpers for Selection Logic ---

def toggle_category(category, key):
    # Get the new value from the checkbox key directly
    new_value = st.session_state[key]
    for doc in st.session_state.documents[category]:
        st.session_state.selections[doc['url']] = new_value
        # Force update the widget state to reflect the change visually
        if doc['url'] in st.session_state:
            st.session_state[doc['url']] = new_value

def perform_search():
    ticker = st.session_state.stock_input.strip().upper()
    if not ticker:
        st.error("Please enter a ticker")
        return

    result = get_webpage_content(ticker)
    if isinstance(result, dict) and "error" in result:
        st.error(f"Error: {result['error']}")
        st.session_state.search_performed = False
        return

    links = parse_html_content(result)
    
    # Group by type
    docs = {'Annual_Report': [], 'Transcript': [], 'PPT': [], 'Credit_Rating': []}
    selections = {}
    
    for link in links:
        if link['type'] in docs:
            docs[link['type']].append(link)
            selections[link['url']] = True # Default to selected
            
    st.session_state.documents = docs
    st.session_state.selections = selections
    st.session_state.search_performed = True
    # Reset "Select All" toggles to True when new search happens
    st.session_state.select_all_Annual_Report = True
    st.session_state.select_all_Transcript = True
    st.session_state.select_all_PPT = True
    st.session_state.select_all_Credit_Rating = True


# --- Helpers for Auto Download ---
def auto_download_file(data, file_name, mime_type):
    """
    Generates a link to download the given data_str and automatically clicks it.
    """
    b64 = base64.b64encode(data).decode()
    payload = f'{mime_type};base64,{b64}'
    html = f"""
        <html>
        <head>
        <title>Start Auto Download</title>
        </head>
        <body>
        <script type="text/javascript">
        var link = document.createElement('a');
        link.href = "data:{payload}";
        link.download = "{file_name}";
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        </script>
        </body>
        </html>
    """
    st.components.v1.html(html, height=0, width=0, scrolling=False)


# --- UI Layout ---

st.markdown("<h1 style='margin-bottom: 1rem; text-align: center;'>Stock Document Fetcher</h1>", unsafe_allow_html=True)

# Search Bar Area - Centered
col_spacer_l, col_search_mid, col_spacer_r = st.columns([1, 2, 1])
with col_search_mid:
    # Use label_visibility="collapsed" to save header space if desired, or keep label for clarity
    st.text_input("Search Stocks", placeholder="Enter Ticker (e.g. RELIANCE)", 
                  key="stock_input", on_change=perform_search, label_visibility="collapsed")
    st.markdown("<p style='text-align:center; color:#666; font-size:0.8rem; margin-top:5px;'>Press Enter to Search</p>", unsafe_allow_html=True)

st.markdown("<div style='margin-bottom: 1rem;'></div>", unsafe_allow_html=True) # Spacer

# Main Content Grid
# Main Content Grid
if st.session_state.search_performed:
    
    # 4 Columns for: Annual Reports, Transcripts, PPTs, Credit Ratings
    grid_cols = st.columns(4)
    
    # --- Helper to render a column ---
    def render_column(col, title, category, select_all_key):
        with col:
            st.markdown(
                f"""<div style='background:#111; border:1px solid #333; padding:10px; border-radius:5px 5px 0 0; display:flex; justify-content:space-between; align-items:center;'>
                <span style='color:white; font-weight:600; font-size:0.9rem;'>{title}</span>
                </div>""", 
                unsafe_allow_html=True
            )
            
            st.checkbox("Select All", key=select_all_key, 
                        on_change=toggle_category, args=(category, select_all_key))
            
            with st.container(height=380, border=True):
                if not st.session_state.documents[category]:
                    st.info(f"No {title} found.")
                
                for doc in st.session_state.documents[category]:
                    is_selected = st.session_state.selections.get(doc['url'], False)
                    checked = st.checkbox(doc['display_name'], value=is_selected, key=doc['url'])
                    st.session_state.selections[doc['url']] = checked

    # Render all 4 columns
    render_column(grid_cols[0], "Annual Reports", "Annual_Report", "select_all_Annual_Report")
    render_column(grid_cols[1], "Transcripts", "Transcript", "select_all_Transcript")
    render_column(grid_cols[2], "Presentations", "PPT", "select_all_PPT")
    render_column(grid_cols[3], "Credit Ratings", "Credit_Rating", "select_all_Credit_Rating")


    # --- Download Section ---
    st.markdown("<br>", unsafe_allow_html=True)
    
    # Calculate what to download
    to_download = []
    for cat in ['Annual_Report', 'Transcript', 'PPT', 'Credit_Rating']:
        for doc in st.session_state.documents[cat]:
            if st.session_state.selections.get(doc['url'], False):
                to_download.append(doc)
    
    if len(to_download) > 0:
        if st.button(f"Start Download ({len(to_download)} Documents)", type="primary"):
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            # Temporary Directory for Download
            with tempfile.TemporaryDirectory() as temp_dir:
                file_contents_for_zip = {}
                failed_downloads = []
                
                # Setup Driver
                driver = None
                try:
                    chrome_options = Options()
                    chrome_options.add_argument("--headless")
                    chrome_options.add_argument("--no-sandbox")
                    chrome_options.add_argument("--disable-gpu")
                    chrome_options.add_argument(f"user-agent={random.choice(USER_AGENTS)}")
                    
                    if os.path.exists("/home/appuser"):
                         driver = webdriver.Chrome(options=chrome_options) 
                    else:
                        service = Service(ChromeDriverManager().install())
                        driver = webdriver.Chrome(service=service, options=chrome_options)
                        
                    driver.set_page_load_timeout(SELENIUM_PAGE_LOAD_TIMEOUT)
                    
                    # Functions imported from previous logic (inlined here for simplicity/completeness)
                    def download_with_driver(url, folder, name, dtype, drv):
                        try:
                            drv.get(url)
                            time.sleep(3)
                            cookies = {c['name']: c['value'] for c in drv.get_cookies()}
                            resp = requests.get(drv.current_url, cookies=cookies, headers={"User-Agent": random.choice(USER_AGENTS)}, stream=True, timeout=30)
                            resp.raise_for_status()
                            ext = get_extension_from_response(resp, drv.current_url, dtype)
                            path = os.path.join(folder, name + ext)
                            with open(path, 'wb') as f:
                                for chunk in resp.iter_content(8192): f.write(chunk)
                            return path, None
                        except Exception as e:
                            return None, str(e)

                    # Loop Downloads
                    total = len(to_download)
                    for i, doc in enumerate(to_download):
                        base_name = format_filename_base(doc['date'], doc['type'])
                        status_text.text(f"Downloading {i+1}/{total}: {base_name}...")
                        
                        # Try direct first
                        path = None
                        try:
                            resp = requests.get(doc['url'], headers={"User-Agent": random.choice(USER_AGENTS)}, timeout=30)
                            if resp.status_code == 200:
                                ext = get_extension_from_response(resp, doc['url'], doc['type'])
                                path = os.path.join(temp_dir, base_name + ext)
                                with open(path, 'wb') as f: f.write(resp.content)
                        except: pass
                        
                        # Fallback to visual (selenium)
                        if not path or os.path.getsize(path) < 1000:
                            path, err = download_with_driver(doc['url'], temp_dir, base_name, doc['type'], driver)
                        
                        if path and os.path.exists(path):
                            with open(path, 'rb') as f:
                                file_contents_for_zip[os.path.basename(path)] = f.read()
                        else:
                            failed_downloads.append(doc['display_name'])
                            
                        progress_bar.progress((i+1)/total)
                        
                except Exception as e:
                    st.error(f"Global Error: {str(e)}")
                finally:
                    if driver: driver.quit()
                
                # Zip
                if file_contents_for_zip:
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                        for name, data in file_contents_for_zip.items():
                            zf.writestr(name, data)
                    
                    st.success(f"Prepared {len(file_contents_for_zip)} documents! Downloading now...")
                    
                    # AUTO DOWNLOAD
                    auto_download_file(
                        zip_buffer.getvalue(), 
                        f"{st.session_state.stock_input.upper()}_Docs.zip", 
                        "application/zip"
                    )
                
                if failed_downloads:
                    st.warning(f"Could not download: {', '.join(failed_downloads)}")
    else:
        st.warning("Select at least one document to download.")

else:
    # Empty State (Vertically Centered placeholder)
    st.markdown("""
        <div style='text-align: center; color: #555; margin-top: 15vh;'>
            <h3>Enter a ticker symbol above to begin</h3>
            <p>Documents will appear here in categorized lists.</p>
        </div>
    """, unsafe_allow_html=True)
