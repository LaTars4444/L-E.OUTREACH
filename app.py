import os
import re
import base64
import time
import random
from email.mime.text import MIMEText
from pypdf import PdfReader
import google_auth_oauthlib.flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from flask import Flask, render_template_string, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "prod_key_123")
app.config['UPLOAD_FOLDER'] = '/tmp'

# --- GOOGLE OAUTH CONFIG ---
CLIENT_CONFIG = {
    "web": {
        "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "redirect_uris": [os.environ.get("REDIRECT_URI")]
    }
}
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

# --- BACKEND HELPERS ---
def find_emails_in_text(text):
    text = text.lower()
    # Fix mashed emails (e.g. .comname@...)
    for tld in ['.com', '.net', '.org', '.edu', '.co.uk']:
        text = text.replace(tld, f"{tld} ")
    return re.findall(r'[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}', text)

def extract_from_pdf(filepath):
    found = set()
    try:
        reader = PdfReader(filepath)
        for page in reader.pages:
            found.update(find_emails_in_text(page.extract_text() or ""))
    except: pass
    return list(found)

def send_gmail(creds, to, sub, body):
    try:
        service = build('gmail', 'v1', credentials=Credentials(**creds))
        msg = MIMEText(body)
        msg['to'], msg['subject'] = to, sub
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        return True
    except: return False

# --- UI TEMPLATE ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Outreach System</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { font-family: -apple-system, system-ui, sans-serif; background-color: #fff; }
        .inp { border: 1px solid #e5e7eb; border-radius: 14px; padding: 16px; width: 100%; outline: none; transition: 0.2s; }
        .inp:focus { border-color: #000; box-shadow: 0 0 0 2px rgba(0,0,0,0.05); }
        .btn-black { background: #000; color: #fff; border-radius: 16px; padding: 18px; font-weight: 700; transition: 0.2s; cursor: pointer; text-align: center; width: 100%; }
        .btn-black:hover { background: #333; transform: translateY(-1px); }
        .crypto-btn { display: flex; align-items: center; justify-content: center; gap: 8px; padding: 14px; border: 1px solid #e5e7eb; border-radius: 14px; font-size: 13px; font-weight: 600; transition: 0.2s; background: #fff; width: 100%; }
        .crypto-btn:hover { background: #fafafa; border-color: #000; }
    </style>
</head>
<body class="min-h-screen flex flex-col items-center justify-center p-6">

    <div class="w-full max-w-lg">
        <header class="mb-12 text-center">
            <h1 class="text-5xl font-black tracking-tighter mb-3">Outreach</h1>
            <p class="text-gray-400 font-medium tracking-wide">Automated System</p>
        </header>

        {% with m = get_flashed_messages() %}
          {% if m %}
            <div class="mb-8 p-5 bg-black text-white rounded-2xl text-sm font-bold shadow-2xl">
                {{ m[0]|safe }}
            </div>
          {% endif %}
        {% endwith %}

        {% if not is_logged_in %}
            <div class="text-center py-20 bg-gray-50 rounded-[40px] border border-gray-100">
                <a href="/login" class="btn-black inline-block px-12">Sign in with Google</a>
            </div>
        {% else %}
            <form action="/process" method="POST" enctype="multipart/form-data" class="space-y-8">
                
                <div class="space-y-4">
                    <div class="flex justify-between items-end px-1">
                        <label class="font-black text-[10px] uppercase tracking-[0.2em] text-gray-400">1. Target Emails</label>
                        <span class="text-[10px] font-bold text-gray-400 uppercase cursor-pointer hover:text-black" onclick="document.getElementById('pdf-drawer').classList.toggle('hidden')">+ Add via PDF</span>
                    </div>
                    <textarea name="manual_emails" placeholder="Paste emails here..." class="inp h-32 font-mono text-sm shadow-sm"></textarea>
                    
                    <div id="pdf-drawer" class="hidden p-4 bg-gray-50 rounded-2xl border-2 border-dashed border-gray-200">
                        <input type="file" name="file" accept=".pdf" class="text-xs font-bold">
                    </div>
                </div>

                <div class="space-y-4">
                    <label class="font-black text-[10px] uppercase tracking-[0.2em] text-gray-400 px-1">2. Campaign Content</label>
                    <input type="text" name="subject" placeholder="Subject Line" class="inp shadow-sm" required>
                    <textarea name="body" placeholder="Your message content..." class="inp h-40 shadow-sm" required></textarea>
                </div>
                
                <div class="space-y-4 bg-gray-50 p-6 rounded-[30px] border border-gray-100">
                    <div class="flex justify-between items-center px-1">
                        <label class="font-black text-[10px] uppercase tracking-[0.2em] text-gray-400">Support Project (Optional)</label>
                        <div class="flex items-center gap-1 font-bold text-sm">
                            <input type="number" id="amt" step="0.01" value="0.05" class="w-12 bg-transparent text-right outline-none">
                            <span class="text-gray-400">UNIT</span>
                        </div>
                    </div>

                    <div class="flex gap-3">
                        <!-- Solana on Left -->
                        <button type="button" onclick="paySol()" class="crypto-btn shadow-sm">
                            <img src="https://cryptologos.cc/logos/solana-sol-logo.svg" class="w-4 h-4">
                            Donate SOL
                        </button>
                        <!-- Bitcoin on Right -->
                        <button type="button" onclick="payBtc()" class="crypto-btn shadow-sm">
                            Donate BTC
                            <img src="https://cryptologos.cc/logos/bitcoin-btc-logo.svg" class="w-4 h-4">
                        </button>
                    </div>
                    <div id="addressBox" class="hidden text-[10px] font-mono bg-white p-3 border rounded-xl text-center break-all text-gray-500"></div>
                </div>

                <button type="submit" class="btn-black text-xl shadow-xl">
                    Launch Campaign 🚀
                </button>
            </form>
            <div class="mt-12 text-center"><a href="/logout" class="text-[10px] font-bold uppercase tracking-widest text-gray-300 hover:text-black transition">End Session</a></div>
        {% endif %}
    </div>

    <script>
        const SOL_WALLET = "4XLckyU64gq1KLwvG71TYEpHps7AKsWoTF3Uu6wD31Zd";
        const BTC_WALLET = "bc1pnq620t2j04lrh9etyhwgxhjs495vtukhpgy7nlenyctpsjfwnh7qgxmzv7";

        function paySol() {
            const amt = document.getElementById('amt').value || '0.05';
            window.location.href = `solana:${SOL_WALLET}?amount=${amt}&label=OutreachSupport`;
            showAddr(SOL_WALLET);
        }

        function payBtc() {
            const amt = document.getElementById('amt').value;
            window.location.href = `bitcoin:${BTC_WALLET}?amount=${amt}`;
            showAddr(BTC_WALLET);
        }

        function showAddr(addr) {
            const box = document.getElementById('addressBox');
            box.innerText = "Address: " + addr;
            box.classList.remove('hidden');
        }
    </script>
</body>
</html>
"""

# --- ROUTES ---

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, is_logged_in=('credentials' in session))

@app.route('/login')
def login():
    try:
        flow = google_auth_oauthlib.flow.Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)
        flow.redirect_uri = CLIENT_CONFIG['web']['redirect_uris'][0]
        auth_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true')
        session['state'] = state
        return redirect(auth_url)
    except Exception as e: return f"OAuth Setup Error: {e}"

@app.route('/callback')
def callback():
    try:
        flow = google_auth_oauthlib.flow.Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES, state=session.get('state'))
        flow.redirect_uri = CLIENT_CONFIG['web']['redirect_uris'][0]
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials
        session['credentials'] = {
            'token': creds.token, 'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri, 'client_id': creds.client_id,
            'client_secret': creds.client_secret, 'scopes': creds.scopes
        }
        return redirect(url_for('index'))
    except: return redirect(url_for('index'))

@app.route('/process', methods=['POST'])
def process():
    if 'credentials' not in session: return redirect(url_for('login'))
    
    manual_text = request.form.get('manual_emails', '')
    emails = set(find_emails_in_text(manual_text))
    
    f = request.files.get('file')
    if f and f.filename.lower().endswith('.pdf'):
        f.save(os.path.join('/tmp', secure_filename(f.filename)))
        emails.update(extract_from_pdf(os.path.join('/tmp', secure_filename(f.filename))))
    
    final_list = list(emails)
    if not final_list:
        flash("System: 0 recipients found.")
        return redirect(url_for('index'))

    success_count = 0
    # Sending loop with human delay
    for i, e in enumerate(final_list):
        if send_gmail(session['credentials'], e, request.form.get('subject'), request.form.get('body')):
            success_count += 1
        
        # Human Delay: 5-15 seconds
        if i < len(final_list) - 1:
            time.sleep(random.randint(5, 15))
    
    flash(f"Success: {success_count} emails delivered to {len(final_list)} targets.")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
