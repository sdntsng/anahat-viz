#!/usr/bin/env python3
"""
Jugnu Data Pipeline
===================
Processes raw CSV mockups into dashboard-ready JSON.
Computes risk scores and classifies panchayats into Red/Blue/Green zones.
"""

import csv
import json
import os
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE, 'raw')
PROC_DIR = os.path.join(BASE, 'processed')
os.makedirs(PROC_DIR, exist_ok=True)

# ============================================================
# ASHA WORKER MAPPING (synthetic assignment per panchayat)
# ============================================================
ASHA_MAP = {
    "पंचायत-1":  {"name": "सुनीता देवी", "phone": "9876543201"},
    "पंचायत-2":  {"name": "रिंकू देवी",  "phone": "9876543202"},
    "पंचायत-3":  {"name": "मीना देवी",  "phone": "9876543203"},
    "पंचायत-5":  {"name": "कविता देवी", "phone": "9876543205"},
    "पंचायत-6":  {"name": "सरस्वती देवी", "phone": "9876543206"},
    "पंचायत-9":  {"name": "अनीता देवी", "phone": "9876543209"},
    "पंचायत-10": {"name": "प्रिया देवी", "phone": "9876543210"},
    "पंचायत-11": {"name": "लक्ष्मी देवी", "phone": "9876543211"},
    "पंचायत-12": {"name": "गीता देवी",  "phone": "9876543212"},
    "पंचायत-15": {"name": "रमा देवी",   "phone": "9876543215"},
    "पंचायत-16": {"name": "सावित्री देवी", "phone": "9876543216"},
    "पंचायत-18": {"name": "पार्वती देवी", "phone": "9876543218"},
    "पंचायत-20": {"name": "दुर्गा देवी", "phone": "9876543220"},
}


def read_csv(filename):
    path = os.path.join(RAW_DIR, filename)
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        return list(reader)


def safe_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def yn_to_bool(val):
    v = str(val).strip().lower()
    return v in ('हाँ', 'yes', 'true', '1', '✓')


def compute_individual_risk(person_records, m2_records, m5_record, m4_record):
    """
    Computes a risk score for a single adolescent.
    Lower (more negative) = higher risk.
    """
    score = 0.0
    flags = []

    # ---- Health (Mockup 2) ----
    hb_values = []
    diseases = []
    dewormed = False

    for r in m2_records:
        hb = safe_float(r.get('एचबी स्तर', ''))
        if hb is not None:
            hb_values.append(hb)
        for d in ['थैलासामिया', 'टीबी', 'मलेरिया', 'मधुमेह']:
            if yn_to_bool(r.get(d, '')):
                diseases.append(d)
        if yn_to_bool(r.get('कृमि का इलाज', '')):
            dewormed = True

    # Anemia scoring (most critical health indicator)
    min_hb = min(hb_values) if hb_values else 13.0
    if min_hb < 10.0:
        score -= 3
        flags.append("severe_anaemia")
    elif min_hb < 11.0:
        score -= 2
        flags.append("moderate_anaemia")
    elif min_hb < 11.5:
        score -= 0.5
        flags.append("mild_anaemia")

    # Disease burden
    unique_diseases = set(diseases)
    for d in unique_diseases:
        score -= 0.5
        flags.append(f"disease:{d}")

    if not dewormed:
        score -= 0.25

    # ---- Menstrual Hygiene (Mockup 1 - girls only) ----
    if person_records:
        pad_shortage = sum(1 for r in person_records if yn_to_bool(r.get('पीरियड्स के सही सामान की कमी है?', '')))
        school_miss = sum(1 for r in person_records if str(r.get('मासिक के दौरान स्कूल जाते हैं?', '')).strip().lower() == 'ना')
        bad_toilet = sum(1 for r in person_records if str(r.get('शौचालय काम कर रहा है?', '')).strip().lower() == 'ना')
        no_restroom = sum(1 for r in person_records if str(r.get('आराम कक्ष/कपड़े बदलने का कमरा', '')).strip().lower() == 'ना')

        total_months = len(person_records)
        if total_months > 0:
            if pad_shortage / total_months > 0.4:
                score -= 1
                flags.append("pad_shortage")
            if school_miss / total_months > 0.4:
                score -= 1
                flags.append("school_absenteeism")
            if bad_toilet / total_months > 0.4:
                score -= 0.5
                flags.append("bad_toilet")
            if no_restroom / total_months > 0.4:
                score -= 0.5
                flags.append("no_restroom")

    # ---- Documentation (Mockup 5) ----
    if m5_record:
        if not yn_to_bool(m5_record.get('क्या आपके पास आधार कार्ड है', '')):
            score -= 0.5
            flags.append("missing_aadhar")
        if not yn_to_bool(m5_record.get('क्या आपके पास बैंक पासबुक है', '')):
            score -= 0.5
            flags.append("missing_bank")
        if not yn_to_bool(m5_record.get('क्या आपके पास राशन कार्ड है', '')):
            score -= 0.5
            flags.append("missing_ration")
        if str(m5_record.get('क्या आपने स्कूल में एडमिशन लिया है', '')).strip().lower() == 'नहीं':
            score -= 1
            flags.append("school_dropout")
        if not yn_to_bool(m5_record.get('क्या आपके पास स्वास्थ्य बीमा कार्ड है', '')):
            score -= 0.5
            flags.append("no_insurance")

    # ---- Scheme Matrix (Mockup 4) ----
    if m4_record:
        months = ['जनवरी','फ़रवरी','मार्च','अप्रैल','मई','जून','जुलाई','अगस्त','सितंबर','अक्टूबर','नवंबर','दिसंबर']
        for m in months:
            val = str(m4_record.get(m, '')).strip().upper()
            if val == 'R':
                score -= 0.1
            elif val == 'P':
                score -= 0.05

    return round(score, 1), flags, min_hb


def main():
    print("[pipeline] Loading raw CSVs...")
    m1 = read_csv('mockup1.csv')   # Menstrual hygiene
    m2 = read_csv('mockup2.csv')   # Health screening
    m3 = read_csv('mockup3.csv')   # Camp attendance
    m4 = read_csv('mockup4.csv')   # Scheme matrix
    m5 = read_csv('mockup5.csv')   # Demographics

    # Index by name
    m1_by_name = defaultdict(list)
    for r in m1:
        m1_by_name[r['नाम']].append(r)

    m2_by_name = defaultdict(list)
    for r in m2:
        m2_by_name[r['नाम']].append(r)

    m3_by_name = defaultdict(list)
    for r in m3:
        m3_by_name[r['नाम']].append(r)

    m4_by_name = {}
    for r in m4:
        m4_by_name[r['नाम']] = r

    m5_by_name = {}
    for r in m5:
        m5_by_name[r['नाम']] = r

    # ============================================================
    # COMPUTE INDIVIDUAL RISK
    # ============================================================
    individuals = []
    panchayat_scores = defaultdict(list)

    for name in m5_by_name:
        rec = m5_by_name[name]
        panchayat = rec.get('ग्राम पंचायत/वार्ड संख्या', 'Unknown')

        score, flags, min_hb = compute_individual_risk(
            m1_by_name.get(name, []),
            m2_by_name.get(name, []),
            rec,
            m4_by_name.get(name)
        )

        # Count camps attended
        camps = m3_by_name.get(name, [])
        camp_count = len(camps)
        camp_topics = list(set(c['संचालन विषय'] for c in camps))

        entry = {
            'name': name,
            'id': rec.get('पहचान संख्या', ''),
            'age': rec.get('जन्मतिथि/उम्र', ''),
            'gender': 'F' if 'महिला' in rec.get('लिंग', '') else 'M',
            'panchayat': panchayat,
            'village': rec.get('गांव / शहर', ''),
            'risk_score': score,
            'risk_zone': 'pending',
            'risk_flags': flags,
            'min_hb': min_hb,
            'camps_attended': camp_count,
            'camp_topics': camp_topics,
            'has_aadhar': yn_to_bool(rec.get('क्या आपके पास आधार कार्ड है', '')),
            'has_bank': yn_to_bool(rec.get('क्या आपके पास बैंक पासबुक है', '')),
            'has_ration': yn_to_bool(rec.get('क्या आपके पास राशन कार्ड है', '')),
            'has_insurance': yn_to_bool(rec.get('क्या आपके पास स्वास्थ्य बीमा कार्ड है', '')),
            'school_admission': str(rec.get('क्या आपने स्कूल में एडमिशन लिया है', '')).strip().lower() == 'हाँ',
            'phone': rec.get('फ़ोन नंबर', ''),
        }
        individuals.append(entry)
        panchayat_scores[panchayat].append(score)

    # ============================================================
    # PERCENTILE-BASED ZONE ASSIGNMENT
    # ============================================================
    all_scores = sorted([i['risk_score'] for i in individuals])
    n = len(all_scores)
    red_threshold = all_scores[int(n * 0.30)]    # bottom 30% = red
    blue_threshold = all_scores[int(n * 0.70)]   # next 40% = blue, top 30% = green

    for i in individuals:
        if i['risk_score'] <= red_threshold:
            i['risk_zone'] = 'red'
        elif i['risk_score'] <= blue_threshold:
            i['risk_zone'] = 'blue'
        else:
            i['risk_zone'] = 'green'

    # ============================================================
    # COMPUTE PANCHAYAT-LEVEL ZONES
    # ============================================================
    panchayat_zones = []
    for panchayat, scores in panchayat_scores.items():
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0
        red_count = sum(1 for s in scores if s <= red_threshold)
        blue_count = sum(1 for s in scores if red_threshold < s <= blue_threshold)
        green_count = sum(1 for s in scores if s > blue_threshold)
        total = len(scores)

        # Panchayat zone by majority
        if red_count >= blue_count and red_count >= green_count:
            zone = 'red'
        elif blue_count >= green_count:
            zone = 'blue'
        else:
            zone = 'green'

        asha = ASHA_MAP.get(panchayat, {"name": "Unknown", "phone": "N/A"})

        panchayat_zones.append({
            'panchayat': panchayat,
            'zone': zone,
            'avg_risk_score': avg_score,
            'total_adolescents': total,
            'red_count': red_count,
            'blue_count': blue_count,
            'green_count': green_count,
            'asha_name': asha['name'],
            'asha_phone': asha['phone'],
        })

    # Sort by risk (most risky first)
    panchayat_zones.sort(key=lambda x: x['avg_risk_score'])
    individuals.sort(key=lambda x: x['risk_score'])

    # ============================================================
    # COMPUTE AGGREGATE KPIs
    # ============================================================
    total_tracked = len(individuals)
    severe_anaemia = sum(1 for i in individuals if i['min_hb'] < 11.0)
    pad_shortage_girls = sum(1 for i in individuals if i['gender'] == 'F' and any('pad_shortage' in f for f in i['risk_flags']))
    total_girls = sum(1 for i in individuals if i['gender'] == 'F')
    pending_schemes = sum(1 for i in individuals if any('scheme_pending' in f or 'scheme_rejected' in f for f in i['risk_flags']))

    # Hb distribution
    hb_severe = sum(1 for i in individuals if i['min_hb'] < 9.0)
    hb_moderate = sum(1 for i in individuals if 9.0 <= i['min_hb'] < 11.0)
    hb_mild = sum(1 for i in individuals if 11.0 <= i['min_hb'] < 12.0)
    hb_normal = sum(1 for i in individuals if i['min_hb'] >= 12.0)

    # Scheme status from Mockup 4
    scheme_sanctioned = 0
    scheme_applied = 0
    scheme_pending_total = 0
    for r in m4:
        for m in ['जनवरी','फ़रवरी','मार्च','अप्रैल','मई','जून','जुलाई','अगस्त','सितंबर','अक्टूबर','नवंबर','दिसंबर']:
            v = str(r.get(m, '')).strip().upper()
            if v == 'S':
                scheme_sanctioned += 1
            elif v == 'A':
                scheme_applied += 1
            elif v == 'P':
                scheme_pending_total += 1

    total_scheme_cells = len(m4) * 12
    scheme_not_applied = total_scheme_cells - scheme_sanctioned - scheme_applied - scheme_pending_total

    # Camp stats from Mockup 3
    camp_topic_counts = defaultdict(int)
    for r in m3:
        camp_topic_counts[r['संचालन विषय']] += 1
    total_camps = len(m3)

    # Alerts (top risky individuals)
    alerts = []
    for i in individuals[:8]:
        if i['risk_zone'] == 'red':
            alert_type = 'critical'
        elif i['risk_zone'] == 'blue':
            alert_type = 'warning'
        else:
            alert_type = 'info'

        # Primary issue - prioritize health flags
        health_priority = ['severe_anaemia', 'moderate_anaemia', 'mild_anaemia', 'disease:टीबी', 'disease:मलेरिया', 'disease:थैलासामिया', 'disease:मधुमेह', 'pad_shortage', 'school_absenteeism', 'school_dropout', 'missing_aadhar']
        primary = 'unknown'
        for p in health_priority:
            if p in i['risk_flags']:
                primary = p
                break
        if primary == 'unknown' and i['risk_flags']:
            primary = i['risk_flags'][0]
        issue_map = {
            'severe_anaemia': 'Critical Anaemia (Hb ' + str(i['min_hb']) + ')',
            'moderate_anaemia': 'Moderate Anaemia (Hb ' + str(i['min_hb']) + ')',
            'pad_shortage': 'No Menstrual Pads',
            'school_absenteeism': 'School Absenteeism',
            'missing_aadhar': 'Aadhar Card Missing',
            'missing_bank': 'Bank Passbook Missing',
            'missing_ration': 'Ration Card Missing',
            'school_dropout': 'School Dropout Risk',
            'scheme_rejected': 'Scheme Rejected',
            'scheme_pending': 'Scheme Pending',
            'disease:टीबी': 'TB Detected',
            'disease:मलेरिया': 'Malaria Detected',
            'disease:थैलासामिया': 'Thalassemia Detected',
            'disease:मधुमेह': 'Diabetes Detected',
        }
        issue_text = issue_map.get(primary, primary)

        alerts.append({
            'id': i['id'],
            'name': i['name'],
            'age': i['age'].split()[0],
            'village': i['village'],
            'panchayat': i['panchayat'],
            'issue': issue_text,
            'type': alert_type,
            'risk_score': i['risk_score'],
            'asha_name': ASHA_MAP.get(i['panchayat'], {}).get('name', 'Unknown'),
            'asha_phone': ASHA_MAP.get(i['panchayat'], {}).get('phone', 'N/A'),
        })

    # ============================================================
    # OUTPUT JSONS
    # ============================================================
    dashboard_metrics = {
        'generated_at': '2026-03-01T00:00:00Z',
        'source_files': ['mockup1.csv', 'mockup2.csv', 'mockup3.csv', 'mockup4.csv', 'mockup5.csv'],
        'kpis': {
            'total_tracked': total_tracked,
            'severe_anaemia': severe_anaemia,
            'pad_shortage': pad_shortage_girls,
            'total_girls': total_girls,
            'pending_schemes': pending_schemes,
            'total_camps': total_camps,
        },
        'hb_distribution': {
            'severe': hb_severe,
            'moderate': hb_moderate,
            'mild': hb_mild,
            'normal': hb_normal,
        },
        'scheme_status': {
            'sanctioned': scheme_sanctioned,
            'applied': scheme_applied,
            'pending': scheme_pending_total,
            'not_applied': scheme_not_applied,
        },
        'camp_topics': dict(camp_topic_counts),
        'panchayat_zones': panchayat_zones,
        'individuals': individuals,
        'alerts': alerts,
    }

    with open(os.path.join(PROC_DIR, 'dashboard-metrics.json'), 'w', encoding='utf-8') as f:
        json.dump(dashboard_metrics, f, ensure_ascii=False, indent=2)

    with open(os.path.join(PROC_DIR, 'individuals.json'), 'w', encoding='utf-8') as f:
        json.dump(individuals, f, ensure_ascii=False, indent=2)

    with open(os.path.join(PROC_DIR, 'panchayat-zones.json'), 'w', encoding='utf-8') as f:
        json.dump(panchayat_zones, f, ensure_ascii=False, indent=2)

    with open(os.path.join(PROC_DIR, 'alerts.json'), 'w', encoding='utf-8') as f:
        json.dump(alerts, f, ensure_ascii=False, indent=2)

    print(f"[pipeline] Processed {total_tracked} individuals across {len(panchayat_scores)} panchayats")
    print(f"[pipeline] Zones -> Red: {sum(1 for p in panchayat_zones if p['zone']=='red')}, Blue: {sum(1 for p in panchayat_zones if p['zone']=='blue')}, Green: {sum(1 for p in panchayat_zones if p['zone']=='green')}")
    print(f"[pipeline] Output written to {PROC_DIR}")


if __name__ == '__main__':
    main()
