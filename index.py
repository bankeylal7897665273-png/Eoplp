from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import requests
import os
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
import random
import datetime

load_dotenv()

app = Flask(__name__)
app.secret_key = 'bankey_otp_secret_key_full_vip'

FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY")
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@gmail.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

# Firebase Auth REST API URLs
AUTH_SIGNUP_URL = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_API_KEY}"
AUTH_LOGIN_URL = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}"

def db_put(path, data):
    requests.put(f"{FIREBASE_DB_URL}/otp_bot/{path}.json", json=data)

def db_patch(path, data):
    requests.patch(f"{FIREBASE_DB_URL}/otp_bot/{path}.json", json=data)

def db_get(path):
    res = requests.get(f"{FIREBASE_DB_URL}/otp_bot/{path}.json")
    return res.json() if res and res.status_code == 200 else None

@app.route('/')
def auth_page():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('auth.html')

@app.route('/api/auth', methods=['POST'])
def authenticate():
    data = request.json
    action = data.get('action')
    email = data.get('email')
    password = data.get('password')
    username = data.get('username', '').strip()

    payload = {"email": email, "password": password, "returnSecureToken": True}
    
    try:
        if action == 'register':
            users = db_get("users") or {}
            for uid, udata in users.items():
                if udata.get('username') == username:
                    return jsonify({"status": "error", "message": "Username already taken!"})

            res = requests.post(AUTH_SIGNUP_URL, json=payload)
            if res.status_code == 200:
                user_id = res.json()['localId']
                db_put(f"users/{user_id}", {
                    "email": email,
                    "username": username,
                    "plan": "None",
                    "otp_limit": 0,
                    "otp_sent": 0,
                    "otp_failed": 0,
                    "api_status": "inactive",
                    "total_apis": 0
                })
                return jsonify({"status": "success", "message": "Account Created Successfully!"})
            else:
                return jsonify({"status": "error", "message": res.json()['error']['message']})
                
        elif action == 'login':
            res = requests.post(AUTH_LOGIN_URL, json=payload)
            if res.status_code == 200:
                user_id = res.json()['localId']
                session['user_id'] = user_id
                if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
                    session['is_admin'] = True
                return jsonify({"status": "success", "message": "Login Successful!"})
            else:
                return jsonify({"status": "error", "message": "Invalid Email or Password!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('auth_page'))
    user_id = session['user_id']
    user_data = db_get(f"users/{user_id}")
    history = db_get(f"history/{user_id}") or {}
    return render_template('dashboard.html', user=user_data, history=history)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth_page'))

@app.route('/api/submit_utr', methods=['POST'])
def submit_utr():
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Not logged in"})
    
    data = request.json
    utr = data.get('utr')
    plan_name = data.get('plan_name')
    otp_amount = data.get('otp_amount')
    price = data.get('price')
    
    # API Details included in UTR Request
    api_name = data.get('api_name')
    app_email = data.get('app_email')
    app_password = data.get('app_password')
    
    user_id = session['user_id']
    
    if len(utr) != 12:
        return jsonify({"status": "error", "message": "UTR must be 12 digits!"})
        
    db_put(f"pending_payments/{utr}", {
        "user_id": user_id,
        "plan_name": plan_name,
        "otp_amount": otp_amount,
        "price": price,
        "api_name": api_name,
        "app_email": app_email,
        "app_password": app_password,
        "status": "pending",
        "date": str(datetime.datetime.now())
    })
    return jsonify({"status": "success", "message": "Request sent to Admin! Wait for approval."})

@app.route('/api/create_api', methods=['POST'])
def create_api():
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Not logged in"})
        
    data = request.json
    name = data.get('name')
    app_email = data.get('app_email')
    app_password = data.get('app_password')
    plan_type = data.get('plan_type') # 'Free'
    user_id = session['user_id']
    
    if len(app_password) != 16:
        return jsonify({"status": "error", "message": "App Password must be 16 characters!"})
        
    user_data = db_get(f"users/{user_id}")
    
    if plan_type == 'Free':
        if user_data.get('total_apis', 0) > 0:
            return jsonify({"status": "error", "message": "Free user can only create 1 API!"})
            
        db_patch(f"users/{user_id}", {
            "plan": "Free",
            "api_name": name,
            "app_email": app_email,
            "app_password": app_password,
            "api_status": "active",
            "otp_limit": 1,
            "total_apis": 1
        })
        return jsonify({"status": "success", "message": "Free API Created Successfully!"})
    
    return jsonify({"status": "error", "message": "Invalid Request"})

# Function to send OTP (Reusable)
def send_otp_logic(user_id, user_data, target_email):
    # Generate 4 Digit OTP
    otp = str(random.randint(1000, 9999))
    sender_email = user_data.get('app_email')
    app_password = user_data.get('app_password')
    
    msg = MIMEText(f"Your OTP is: {otp}\n\nPowered by VIP OTP API System ☠️")
    msg['Subject'] = 'Verification OTP'
    msg['From'] = sender_email
    msg['To'] = target_email
    
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(sender_email, app_password)
        server.send_message(msg)
        server.quit()
        
        # Update DB Stats
        sent = user_data.get('otp_sent', 0)
        db_patch(f"users/{user_id}", {"otp_sent": sent + 1})
        
        # Save History
        history_id = str(random.randint(10000, 99999))
        db_put(f"history/{user_id}/{history_id}", {
            "email": target_email,
            "otp": otp,
            "status": "Success",
            "date": str(datetime.datetime.now())
        })
        
        # 1-Minute Expiry Logic
        expiry_time = datetime.datetime.now() + datetime.timedelta(minutes=1)
        db_patch(f"active_otps/{target_email.replace('.', '_')}", {
            "otp": otp,
            "expiry": str(expiry_time)
        })
        
        return True, otp
    except Exception as e:
        failed = user_data.get('otp_failed', 0)
        db_patch(f"users/{user_id}", {"otp_failed": failed + 1})
        return False, str(e)

# The Main API Endpoint for Generating OTP
@app.route('/<username>/<target_email>')
def send_user_otp(username, target_email):
    users = db_get("users") or {}
    user_id = None
    user_data = None
    
    for uid, udata in users.items():
        if udata.get('username') == username:
            user_id = uid
            user_data = udata
            break
            
    if not user_data or user_data.get('api_status') != 'active':
        return jsonify({"status": "error", "message": "Invalid Username API or Inactive"})
        
    limit = user_data.get('otp_limit', 0)
    sent = user_data.get('otp_sent', 0)
    
    if str(limit) != "Unlimited" and sent >= int(limit):
        return jsonify({"status": "error", "message": "OTP Limit Reached! Upgrade Plan."})
        
    success, otp_or_error = send_otp_logic(user_id, user_data, target_email)
    
    if success:
        return jsonify({"status": "success", "message": "OTP Sent Successfully", "otp": otp_or_error})
    else:
        return jsonify({"status": "error", "message": "Failed to send OTP. Check App Password."})

# 1-Minute Auto-Expire Verification Endpoint
@app.route('/<username>/verify', methods=['POST'])
def verify_user_otp(username):
    data = request.json
    target_email = data.get('email')
    user_otp = data.get('otp')
    
    users = db_get("users") or {}
    user_id = None
    user_data = None
    for uid, udata in users.items():
        if udata.get('username') == username:
            user_id = uid
            user_data = udata
            break

    if not user_data:
        return jsonify({"status": "error", "message": "Invalid API"})
        
    safe_email = target_email.replace('.', '_')
    otp_data = db_get(f"active_otps/{safe_email}")
    
    if not otp_data:
        return jsonify({"status": "error", "message": "No active OTP found for this email."})
        
    expiry_time = datetime.datetime.strptime(otp_data['expiry'], "%Y-%m-%d %H:%M:%S.%f")
    
    if datetime.datetime.now() > expiry_time:
        # OTP Expired -> Automatically send a new one
        success, new_otp = send_otp_logic(user_id, user_data, target_email)
        if success:
             return jsonify({
                 "status": "expired_resend", 
                 "message": "1 Minute limit over! Old OTP expired. Auto-generated and sent a NEW OTP to email. Please verify with the new OTP."
             })
        else:
             return jsonify({"status": "error", "message": "OTP expired, but failed to send a new one."})
             
    if user_otp == otp_data['otp']:
        # Delete OTP after successful verification
        requests.delete(f"{FIREBASE_DB_URL}/otp_bot/active_otps/{safe_email}.json")
        return jsonify({"status": "success", "message": "Account Verified Successfully!"})
    else:
        return jsonify({"status": "error", "message": "Invalid OTP entered."})

# Admin Panel
@app.route('/admin')
def admin_panel():
    if not session.get('is_admin'):
        return "Access Denied", 403
    payments = db_get("pending_payments") or {}
    return render_template('admin.html', payments=payments)

@app.route('/admin/approve/<utr>')
def approve_utr(utr):
    if not session.get('is_admin'):
        return "Access Denied", 403
        
    payment = db_get(f"pending_payments/{utr}")
    if payment:
        user_id = payment['user_id']
        total = db_get(f"users/{user_id}/total_apis") or 0
        db_patch(f"users/{user_id}", {
            "plan": payment['plan_name'],
            "otp_limit": payment['otp_amount'],
            "api_name": payment['api_name'],
            "app_email": payment['app_email'],
            "app_password": payment['app_password'],
            "api_status": "active",
            "total_apis": total + 1
        })
        db_put(f"pending_payments/{utr}/status", "Approved")
    return redirect(url_for('admin_panel'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
