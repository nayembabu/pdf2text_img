# PDF Extractor — Web Version

## লোকাল রান করুন

```bash
pip install -r requirements.txt
python app.py
# http://localhost:5000 এ যান
```

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
