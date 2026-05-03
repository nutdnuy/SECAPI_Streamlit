# SEC Fund NAV Downloader

Streamlit app สำหรับโหลดข้อมูล NAV กองทุนรวมไทย จาก SEC OpenAPI

## ติดตั้ง

```bash
pip install -r requirements.txt
```

## รัน

```bash
streamlit run app.py
```

## ใช้งาน

1. ขอ API key (ฟรี) จาก https://api.sec.or.th แล้วใส่ที่ sidebar
2. เลือก บลจ → เลือกกองทุน → เลือกช่วงวันที่
3. กด "โหลด NAV" → ดูกราฟ/ตาราง → ดาวน์โหลด CSV

## API Endpoints ที่ใช้

- `GET /FundFactsheet/fund/amc` — รายชื่อ บลจ
- `GET /FundFactsheet/fund/amc/{unique_id}` — รายชื่อกองทุนของ บลจ
- `GET /FundDailyInfo/{proj_id}/dailynav/{start}/{end}` — NAV รายวัน
