from flask import Flask, request, jsonify, render_template, send_file
import fitz
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_r
import json
import os
import re
import unicodedata
import numpy as np
from PIL import Image, ImageOps, ImageFilter
import base64
import io
import uuid

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "output"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)


# ══════════════════════════════════════════════
# Text Helper Functions
# ══════════════════════════════════════════════

## Bengali character sets
_VOWEL_SET = set('\u09BE\u09BF\u09C0\u09C1\u09C2\u09C3\u09C4\u09C7\u09C8\u09CB\u09CC\u09CD\u09D7')
_CONS_SET  = set(chr(c) for c in range(0x0995, 0x09BA)) | {'\u09CE'} | set(chr(c) for c in range(0x09DC, 0x09E0))
_NUKTA     = '\u09BC'
_ANUSVAR   = set('\u0981\u0982\u0983')
_HASANTA   = '\u09CD'
_ALL_BN    = _VOWEL_SET | _CONS_SET | _ANUSVAR | {_NUKTA}


def fix_bengali_spacing(text: str) -> str:
    """
    PDF থেকে extract করা বাংলা টেক্সটে ভাঙা অক্ষরের মাঝে
    অপ্রয়োজনীয় space সরিয়ে সঠিক করে।
    যেমন: "ক ুমিল্লা" → "কুমিল্লা", "চ ট্টগ্রাম" → "চট্টগ্রাম"
    """
    if not text:
        return text

    chars  = list(text)
    result = []
    i      = 0

    while i < len(chars):
        ch = chars[i]

        if ch == ' ' and i + 1 < len(chars):
            next_ch = chars[i + 1]
            prev_ch = result[-1] if result else ''

            # Rule 1: space এর পরে vowel sign → space সরাও
            # "ক ু" → "কু",  "হ ু" → "হু"
            if next_ch in _VOWEL_SET and prev_ch in _ALL_BN:
                i += 1
                continue

            # Rule 2: space এর পরে nukta / anusvar → সরাও
            if next_ch in (_ANUSVAR | {_NUKTA}) and prev_ch in _ALL_BN:
                i += 1
                continue

            # Rule 3: consonant + space + consonant + hasanta (যুক্তবর্ণ ভেঙেছে)
            # "চ ট্ট" → "চট্ট"
            if (prev_ch in _CONS_SET and next_ch in _CONS_SET
                    and i + 2 < len(chars) and chars[i + 2] == _HASANTA):
                i += 1
                continue

            # Rule 4: একা consonant + space + consonant + vowel → জোড়া লাগাও
            # "ব রি" → "বরি"  কিন্তু "র হু" নয় (র এর আগে vowel আছে → আলাদা শব্দ)
            if (prev_ch in _CONS_SET and next_ch in _CONS_SET
                    and i + 2 < len(chars) and chars[i + 2] in _VOWEL_SET):
                prev_prev = result[-2] if len(result) >= 2 else ''
                if prev_prev not in _VOWEL_SET:
                    i += 1
                    continue

        result.append(ch)
        i += 1

    return ''.join(result)


def sanitize_bengali(text: str) -> str:
    """repeated visarga / vowel sign collapse"""
    VOWEL_SIGNS = set(chr(c) for c in range(0x09BE, 0x09CD + 1))
    MARKS = set(['\u0981', '\u0982', '\u0983', '\u09BC', '\u09D7'])
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '\u0983':
            out.append(ch)
            j = i + 1
            while j < len(text) and (text[j] == '\u0983' or text[j] in VOWEL_SIGNS):
                j += 1
            i = j
            continue
        if ch in VOWEL_SIGNS:
            if out and out[-1] in VOWEL_SIGNS:
                i += 1
                continue
        if ch in MARKS:
            if out and out[-1] == ch:
                i += 1
                continue
        out.append(ch)
        i += 1
    return ''.join(out)


def clean_text(text):
    if not text:
        return ""
    text = unicodedata.normalize('NFC', text)
    text = re.sub(r'\s+', ' ', text)
    text = fix_bengali_spacing(text)   # ← PDF spacing fix (নতুন)
    text = sanitize_bengali(text)       # ← duplicate mark fix
    return text.strip()


def extract_field(text, pattern, group=1):
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return clean_text(match.group(group)) if match and match.group(group) else ""


def extract_bounded(text, start_pattern, end_pattern):
    pattern = re.compile(start_pattern + r'(.*?)' + end_pattern, re.DOTALL | re.IGNORECASE)
    match = pattern.search(text)
    return clean_text(match.group(1)) if match else ""


ADDR_LABELS = [
    ("division",                         r"Division"),
    ("district",                         r"District"),
    ("rmo",                              r"RMO"),
    ("city_corporation_or_municipality", r"City\s+Corporation\s+Or\s+Municipality"),
    ("upozila",                          r"Upozila"),
    ("union_ward",                       r"Union/Ward"),
    ("mouza_moholla",                    r"Mouza/Moholla"),
    ("additional_mouza_moholla",         r"Additional\s+Mouza/Moholla"),
    ("ward_for_union_porishod",          r"Ward\s+For\s+Union\s+Porishod"),
    ("village_road",                     r"Village/Road"),
    ("additional_village_road",          r"Additional\s+Village/Road"),
    ("home_holding_no",                  r"Home/Holding\s+No"),
    ("post_office",                      r"Post\s+Office"),
    ("postal_code",                      r"Postal\s+Code"),
    ("region",                           r"Region"),
]

LABEL_STARTERS = [
    "Division", "District", "RMO", "City Corporation Or Municipality",
    "Upozila", "Union/Ward", "Mouza/Moholla", "Additional Mouza/Moholla",
    "Ward For Union Porishod", "Village/Road", "Additional Village/Road",
    "Home/Holding No", "Post Office", "Postal Code", "Region"
]

def _starts_with_any_label(s):
    s = s.strip()
    if not s:
        return False
    low = s.lower()
    return any(low.startswith(lab.lower()) for lab in LABEL_STARTERS)


def parse_addr_block(block):
    if not block:
        return {}
    text = clean_text(block)
    hits = []
    for key, pat in ADDR_LABELS:
        m = re.search(rf"\b{pat}\b", text, flags=re.IGNORECASE)
        if m:
            hits.append((m.start(), m.end(), key))
    if not hits:
        return {}
    hits.sort(key=lambda x: x[0])
    addr = {}
    for i, (start, end, key) in enumerate(hits):
        next_start = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        raw_val = text[end:next_start].strip()
        val = clean_text(raw_val)
        if not val or _starts_with_any_label(val):
            val = ""
        if key == "ward_for_union_porishod" and val and not re.fullmatch(r"\d+", val):
            val = ""
        addr[key] = val
    for key, _ in ADDR_LABELS:
        addr.setdefault(key, "")
    return addr


# ══════════════════════════════════════════════
# Image Helper
# ══════════════════════════════════════════════

def remove_border(binary_arr, inner_pad=8):
    h_img, w_img = binary_arr.shape
    rows_dark    = (binary_arr == 0).sum(axis=1)
    cols_dark    = (binary_arr == 0).sum(axis=0)
    border_row   = w_img * 0.5
    border_col   = h_img * 0.5

    top = 0
    for i in range(h_img):
        if rows_dark[i] > border_row: top = i + 1
        elif top > 0: break

    bottom = h_img
    for i in range(h_img - 1, -1, -1):
        if rows_dark[i] > border_row: bottom = i
        elif bottom < h_img: break

    left = 0
    for i in range(w_img):
        if cols_dark[i] > border_col: left = i + 1
        elif left > 0: break

    right = w_img
    for i in range(w_img - 1, -1, -1):
        if cols_dark[i] > border_col: right = i
        elif right < w_img: break

    return binary_arr[
        max(0, top   + inner_pad) : min(h_img, bottom - inner_pad),
        max(0, left  + inner_pad) : min(w_img, right  - inner_pad),
    ]


def pil_to_base64(img: Image.Image, fmt="PNG") -> str:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ══════════════════════════════════════════════
# Core PDF Parser
# ══════════════════════════════════════════════

def parse_nid_pdf(pdf_path):
    result = {}

    # ── JSON ডাটা ──
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        full_text += page.get_text("text") + "\n"
    doc.close()
    full_text = clean_text(full_text)

    data = {}
    data["national_id"]           = extract_field(full_text, r'National ID\s+([0-9]+)')
    data["pin"]                   = extract_field(full_text, r'Pin\s+([0-9]+)')
    data["status"]                = extract_field(full_text, r'Status\s+(\w+)')
    data["afis_status"]           = extract_field(full_text, r'Afis Status\s+([\w_]+)')
    data["lock_flag"]             = extract_field(full_text, r'Lock Flag\s+(\w)')
    data["voter_no"]              = extract_field(full_text, r'Voter No\s+([0-9]+)')
    data["form_no"]               = extract_field(full_text, r'Form No\s+([\w]+)')
    data["sl_no"]                 = extract_field(full_text, r'Sl No\s+([0-9]+)')
    data["tag"]                   = extract_field(full_text, r'Tag\s+([\w_]+)')
    data["name"] = {
        "bangla":  extract_field(full_text, r'Name\(Bangla\)\s+(.*?)\s+Name\(English\)', 1),
        "english": extract_field(full_text, r'Name\(English\)\s+(.*?)\s+Date of Birth', 1),
    }
    data["date_of_birth"]         = extract_field(full_text, r'Date of Birth\s+([0-9-]+)')
    data["birth_place"]           = extract_field(full_text, r'Birth Place\s+(.*?)\s+Birth Other')
    data["birth_registration_no"] = extract_field(full_text, r'Birth Registration No\s+([0-9]*)')
    data["father_name"]           = extract_field(full_text, r'Father Name\s+(.*?)\s+Mother Name')
    data["mother_name"]           = extract_field(full_text, r'Mother Name\s+(.*?)\s+(1st )?Spouse Name')
    spouse_block                  = extract_bounded(full_text, r'(1st )?Spouse Name', r'Gender')
    data["spouse_name"]           = clean_text(spouse_block)
    data["gender"]                = extract_field(full_text, r'Gender\s+(\w+)')
    data["marital_status"]        = extract_field(full_text, r'Marital\s+(\w+)')
    data["occupation"]            = extract_field(full_text, r'Occupation\s+(.*?)\s+Disability')

    present_block   = extract_bounded(full_text, r'Present Address',   r'(Permanent Address|Foreign Address|Education|Voter Documents)')
    permanent_block = extract_bounded(full_text, r'Permanent Address', r'(Foreign Address|Education|Voter Documents|Email)')
    foreign_block   = extract_bounded(full_text, r'Foreign Address',   r'(Education|Voter Documents|Email)')
    data["present_address"]   = parse_addr_block(present_block)
    data["permanent_address"] = parse_addr_block(permanent_block)
    data["foreign_address"]   = parse_addr_block(foreign_block or "")

    blood_block  = extract_bounded(full_text, r'Blood Group', r'TIN|Driving|Passport|Laptop ID')
    blood        = clean_text(blood_block)
    data["blood_group"] = blood if re.match(r'^[ABOAB+-]+$', blood) else ""
    data["education"]   = extract_field(full_text, r'Education\s+(.*?)\s+(Education Other|Education Sub|Identification)')
    data["laptop_id"]   = extract_field(full_text, r'Laptop ID\s+([\w_/]+)')
    data["nid_father"]  = extract_field(full_text, r'NID Father\s+([0-9]*)')
    data["religion"]    = extract_field(full_text, r'Religion\s+(\w+)')
    data["voter_area"]  = extract_field(full_text, r'Voter Area\s+(.*?)\s+Voter At')
    data["voter_at"]    = extract_field(full_text, r'Voter At\s+(\w+)')
    data["voter_documents"] = [d for d in ["DATA ENTRY PROOF COPY", "OTHER", "VOTER FORM PAGE ONE", "VOTER FORM PAGE TWO"] if d in full_text]
    for f in ["disability", "disability_other", "tin", "driving", "passport", "phone", "mobile", "email", "nid_mother", "nid_spouse"]:
        data[f] = ""
    data["no_finger"]       = extract_field(full_text, r'No Finger\s+([0-9])')
    data["no_finger_print"] = extract_field(full_text, r'No Finger Print\s+([0-9])')
    result["data"] = data

    # ── Photo: fitz দিয়ে সরাসরি extract ──
    doc = fitz.open(pdf_path)
    img_count = 0
    photo_b64 = None
    for page in doc:
        for img_info in page.get_images(full=True):
            if img_count == 0:
                xref       = img_info[0]
                base_image = doc.extract_image(xref)
                photo_b64  = base64.b64encode(base_image["image"]).decode("utf-8")
                result["photo_ext"] = base_image["ext"]
            img_count += 1
    doc.close()
    result["photo"] = photo_b64

    # ── Signature: pypdfium2 দিয়ে smart type detection + processing ──
    sig_b64 = None
    try:
        doc2        = pdfium.PdfDocument(pdf_path)
        page        = doc2[0]
        page_height = page.get_height()
        IMAGE_TYPE  = pdfium_r.FPDF_PAGEOBJ_IMAGE
        SCALE       = 4

        img_objects = list(page.get_objects(filter=[IMAGE_TYPE]))

        if len(img_objects) >= 2:
            obj = img_objects[1]  # signature object

            # ── Direct bitmap দিয়ে type detect করা ──
            direct_bitmap = obj.get_bitmap()
            direct_pil    = direct_bitmap.to_pil()
            direct_arr    = np.array(direct_pil.convert("L"))
            direct_mean   = direct_arr.mean()
            direct_unique = len(np.unique(direct_arr))

            if direct_mean < 50 or direct_unique < 20:
                # ── Type A: Transparent/Vector ──
                # page render করে crop → threshold → border removal
                bitmap   = page.render(scale=SCALE)
                pil_page = bitmap.to_pil()
                matrix   = obj.get_matrix()
                x, y, w, h = matrix.e, matrix.f, matrix.a, matrix.d
                PAD  = 20
                crop = pil_page.crop((
                    max(0, int(x * SCALE) - PAD),
                    max(0, int((page_height - y - h) * SCALE) - PAD),
                    min(pil_page.width,  int((x + w) * SCALE) + PAD),
                    min(pil_page.height, int((page_height - y) * SCALE) + PAD),
                ))
                gray       = crop.convert("L")
                auto       = ImageOps.autocontrast(gray, cutoff=1)
                arr        = np.array(auto)
                binary     = np.where(arr < 128, 0, 255).astype(np.uint8)
                inner      = remove_border(binary, inner_pad=8)
                result_img = Image.fromarray(inner).filter(ImageFilter.SHARPEN)

            else:
                # ── Type B: Raster Image ──
                # direct bitmap → autocontrast → white bg trim
                gray        = direct_pil.convert("L")
                auto        = ImageOps.autocontrast(gray, cutoff=2)
                arr         = np.array(auto)
                trimmed     = np.where(arr > 240, 255, arr).astype(np.uint8)
                result_img  = Image.fromarray(trimmed).filter(ImageFilter.SHARPEN)

            sig_b64 = pil_to_base64(result_img)

        doc2.close()
    except Exception as e:
        print(f"Signature error: {e}")

    result["signature"] = sig_b64
    return result


# ══════════════════════════════════════════════
# Routes
# ══════════════════════════════════════════════

@app.route('/')
def home():
    return render_template('index.html')


@app.route('/upload-pdf', methods=['POST'])
def upload_pdf():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "কোনো ফাইল পাওয়া যায়নি"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "ফাইল সিলেক্ট করা হয়নি"}), 400
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({"status": "error", "message": "শুধুমাত্র PDF ফাইল গ্রহণযোগ্য"}), 400

    unique_name = f"{uuid.uuid4().hex}.pdf"
    file_path   = os.path.join(UPLOAD_FOLDER, unique_name)
    file.save(file_path)

    try:
        result = parse_nid_pdf(file_path)
        return jsonify({"status": "success", "filename": file.filename, **result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
