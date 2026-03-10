# NID PDF Extractor — Web Version

## লোকাল রান করুন

```bash
pip install -r requirements.txt
python app.py
# http://localhost:5000 এ যান
```

## হোস্ট করুন

### ✅ Render.com (ফ্রি)
1. GitHub-এ এই ফোল্ডারটি push করুন
2. https://render.com → New → Web Service
3. Repository সিলেক্ট করুন
4. Build Command: `pip install -r requirements.txt`
5. Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
6. Deploy করুন ✅

### ✅ Railway.app (ফ্রি)
1. GitHub-এ push করুন
2. https://railway.app → New Project → Deploy from GitHub
3. Automatically detect করবে ✅

### ✅ VPS / Ubuntu Server
```bash
pip install -r requirements.txt
gunicorn app:app --bind 0.0.0.0:5000 --workers 2 --timeout 120 --daemon
```

## ফাইল স্ট্রাকচার
```
nid_web/
├── app.py              ← মূল Flask অ্যাপ
├── requirements.txt    ← লাইব্রেরি লিস্ট
├── Procfile            ← Render/Railway এর জন্য
├── templates/
│   └── index.html      ← ওয়েব ইন্টারফেস
└── uploads/            ← অস্থায়ী ফাইল (auto-created)
```
