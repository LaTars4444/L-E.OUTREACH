import os
import re
import base64
from email.mime.text import MIMEText
from pypdf import PdfReader
import google_auth_oauthlib.flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from flask import Flask, render_template_string, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "fallback_secret")
app.config['UPLOAD_FOLDER'] = '/tmp'

# --- CONFIG ---
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

# --- BACKEND LOGIC ---
def extract_emails(filepath):
    emails = set()
    try:
        reader = PdfReader(filepath)
        for page in reader.pages:
            text = page.extract_text()
            found = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
            emails.update(found)
    except Exception as e:
        print(f"PDF Error: {e}")
    return list(emails)

def send_gmail(creds_dict, to_email, subject, body_text):
    try:
        creds = Credentials(**creds_dict)
        service = build('gmail', 'v1', credentials=creds)
        message = MIMEText(body_text)
        message['to'] = to_email
        message['subject'] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        return True
    except Exception as e:
        print(f"Email Error: {e}")
        return False

# --- UI ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Outreach System</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { font-family: -apple-system, system-ui, sans-serif; }
        .btn { background: #000; color: #fff; transition: 0.2s; }
        .btn:hover { background: #333; transform: translateY(-1px); }
        .inp { border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; width: 100%; outline: none; }
        .inp:focus { border-color: #000; }
    </style>
</head>
<body class="bg-white text-gray-900 h-screen flex flex-col items-center justify-center">
    <div class="w-full max-w-md p-8">
        <h1 class="text-3xl font-bold mb-6 tracking-tight">Outreach System</h1>

        {% with messages = get_flashed_messages() %}
          {% if messages %}
            <div class="mb-4 p-3 bg-gray-100 rounded text-sm font-medium">{{ messages[0] }}</div>
          {% endif %}
        {% endwith %}

        {% if not is_logged_in %}
            <a href="/login" class="btn w-full py-3 rounded-lg font-medium flex justify-center">Sign in with Google</a>
        {% else %}
            <form action="/process" method="POST" enctype="multipart/form-data" class="space-y-4">
                <div>
                    <label class="block text-xs font-bold uppercase mb-1">1. Upload Leads (PDF)</label>
                    <input type="file" name="file" accept=".pdf" class="inp" required>
                </div>
                <div>
                    <label class="block text-xs font-bold uppercase mb-1">2. Email Content</label>
                    <input type="text" name="subject" placeholder="Subject" class="inp mb-2" required>
                    <textarea name="body" placeholder="Message..." class="inp h-24" required></textarea>
                </div>
                
                <!-- PAYMENT WALL -->
                <div class="p-4 border rounded-xl bg-gray-50">
                    <label class="block text-xs font-bold uppercase mb-2">Donate (SOL)</label>
                    <div class="flex gap-2 mb-2">
                        <input type="number" id="amt" step="0.01" value="0.05" class="inp" oninput="upd()">
                        <span class="flex items-center px-3 bg-gray-200 rounded font-bold">SOL</span>
                    </div>
                    <a id="pay" href="#" target="_blank" class="block w-full text-center py-2 border border-black rounded-lg text-xs font-bold hover:bg-black hover:text-white transition">SEND SOL</a>
                </div>

                <button type="submit" class="btn w-full py-3 rounded-lg font-bold text-lg">LAUNCH CAMPAIGN</button>
            </form>
            <div class="mt-4 text-center"><a href="/logout" class="text-xs text-gray-400">Logout</a></div>
        {% endif %}
    </div>
    <script>
        const w = "4XLckyU64gq1KLwvG71TYEpHps7AKsWoTF3Uu6wD31Zd";
        function upd() {
            const v = document.getElementById('amt').value || '0.05';
            document.getElementById('pay').href = `solana:${w}?amount=${v}&label=Donation`;
        }
        upd();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, is_logged_in=('credentials' in session))

@app.route('/login')
def login():
    try:
        flow = google_auth_oauthlib.flow.Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)
        flow.redirect_uri = CLIENT_CONFIG['web']['redirect_uris'][0]
        authorization_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true')
        session['state'] = state
        return redirect(authorization_url)
    except Exception as e:
        return f"Login Error: {e}"

@app.route('/callback')
def callback():
    try:
        state = session.get('state')
        flow = google_auth_oauthlib.flow.Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES, state=state)
        flow.redirect_uri = CLIENT_CONFIG['web']['redirect_uris'][0]
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials
        session['credentials'] = {
            'token': creds.token, 'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri, 'client_id': creds.client_id,
            'client_secret': creds.client_secret, 'scopes': creds.scopes
        }
        return redirect(url_for('index'))
    except:
        return redirect(url_for('index'))

@app.route('/process', methods=['POST'])
def process():
    if 'credentials' not in session: return redirect(url_for('login'))
    f = request.files.get('file')
    if f and f.filename.lower().endswith('.pdf'):
        try:
            path = os.path.join('/tmp', secure_filename(f.filename))
            f.save(path)
            emails = extract_emails(path)
            if not emails:
                flash("No emails found in PDF.")
                return redirect(url_for('index'))
            
            count = 0
            for e in emails:
                if send_gmail(session['credentials'], e, request.form.get('subject'), request.form.get('body')):
                    count += 1
            flash(f"Sent {count} emails!")
            return redirect(url_for('index'))
        except Exception as e:
            flash(f"Error: {e}")
            return redirect(url_for('index'))
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    app.run(debug=True)
