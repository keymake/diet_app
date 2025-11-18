import streamlit as st
from datetime import datetime, timedelta
import pandas as pd
import json
from supabase import create_client, Client

# -------------------- Supabase 접속 --------------------
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_ANON_KEY"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

TABLE = "diet_records"

# -------------------- 공통 함수 --------------------

def get_kst_now():
    return datetime.utcnow() + timedelta(hours=9)

def insert_record(date, weight, status, calories, score):
    data = {
        "date": date,
        "weight": weight,
        "status": status,
        "calories": calories,
        "score": score
    }
    supabase.table(TABLE).insert(data).execute()

def update_record(date, weight=None, status=None, calories=None, score=None):
    update_data = {}
    if weight is not None:
        update_data["weight"] = weight
    if status is not None:
        update_data["status"] = status
    if calories is not None:
        update_data["calories"] = calories
    if score is not None:
        update_data["score"] = score

    supabase.table(TABLE).update(update_data).eq("date", date).execute()

def get_record(date):
    res = supabase.table(TABLE).select("*").eq("date", date).execute()
    if res.data:
        return res.data[0]
    return None

def get_last_T(before_date):
    res = (
        supabase.table(TABLE)
        .select("*")
        .lt("date", before_date)
        .eq("status", "T")
        .order("date", desc=False)
        .execute()
    )
    if res.data:
        return res.data[-1]
    return None

def get_recent_30():
    res = (
        supabase.table(TABLE)
        .select("*")
        .order("date", desc=False)
        .execute()
    )
    return res.data[-30:] if res.data else []

def fill_missing_F(last_t_date, today_date):
    missing = []
    last_dt = datetime.strptime(last_t_date, "%Y-%m-%d")
    today_dt = datetime.strptime(today_date, "%Y-%m-%d")

    for i in range(1, (today_dt - last_dt).days):
        d = (last_dt + timedelta(days=i)).strftime("%Y-%m-%d")
        if get_record(d) is None:
            supabase.table(TABLE).insert({
                "date": d,
                "status": "F",
                "weight": None,
                "calories": {},
                "score": None
            }).execute()
            missing.append(d)
    return len(missing)

def calc_score(prev_weight, today_weight, num_f):
    score = 0.0
    if prev_weight is not None:
        diff = today_weight - prev_weight
        step = int(abs(diff) / 0.1)
        if diff < 0:
            score += step * 0.2
        elif diff > 0:
            score -= step * 0.2
    score -= num_f * 0.3
    return round(score, 2)

# -------------------- A 화면 --------------------

def page_A():
    st.title("성진 다이어트 프로그램 – A 화면 (기록/포인트)")

    now = get_kst_now()
    today = now.strftime("%Y-%m-%d")
    st.write(f"한국 기준 날짜: **{today}**")

    today_record = get_record(today)

    # 입력칸 유지
    default_weight = today_record["weight"] if today_record else 0.0
    default_cal = today_record["calories"] if today_record else {}

    weight = st.number_input(
        "오늘 몸무게 (kg)", step=0.1, format="%.1f",
        value=float(default_weight or 0.0)
    )

    col1, col2 = st.columns(2)
    with col1:
        bf = st.text_input("아침 식단", default_cal.get("아침", ""))
        lu = st.text_input("점심 식단", default_cal.get("점심", ""))
        di = st.text_input("저녁 식단", default_cal.get("저녁", ""))
        sn = st.text_input("간식", default_cal.get("간식", ""))

    with col2:
        kcal_bf = st.number_input("아침 칼로리", min_value=0, step=10, value=default_cal.get("kcal_bf", 0))
        kcal_lu = st.number_input("점심 칼로리", min_value=0, step=10, value=default_cal.get("kcal_lu", 0))
        kcal_di = st.number_input("저녁 칼로리", min_value=0, step=10, value=default_cal.get("kcal_di", 0))
        kcal_sn = st.number_input("간식 칼로리", min_value=0, step=10, value=default_cal.get("kcal_sn", 0))

    total_kcal = kcal_bf + kcal_lu + kcal_di + kcal_sn
    st.write(f"총합 칼로리: **{total_kcal}kcal**")

    cb_dict = {
        "아침": bf, "점심": lu, "저녁": di, "간식": sn,
        "kcal_bf": kcal_bf, "kcal_lu": kcal_lu,
        "kcal_di": kcal_di, "kcal_sn": kcal_sn
    }

    if st.button("오늘 T 기록 저장"):
        last_t = get_last_T(today)
        if last_t:
            num_f = fill_missing_F(last_t["date"], today)
            prev_weight = last_t["weight"]
        else:
            num_f = 0
            prev_weight = None

        today_score = calc_score(prev_weight, weight, num_f)

        # 이미 기록 있으면 업데이트
        if today_record:
            update_record(today, weight, "T", cb_dict, today_score)
        else:
            insert_record(today, weight, "T", cb_dict, today_score)

        st.success(f"T 기록 저장됨! 오늘 점수: {today_score}")

    if st.button("B 화면으로 이동"):
        st.session_state["page"] = "B"

# -------------------- B 화면 --------------------

def page_B():
    st.title("성진 다이어트 프로그램 – B 화면 (그래프/표)")

    data = get_recent_30()
    if not data:
        st.write("기록 없음.")
        return

    df = pd.DataFrame(data)
    df["weight"] = df["weight"].fillna("-")

    st.dataframe(df[["date", "weight", "status", "score"]])

    # 그래프: T만 연결
    t_df = df[df["status"] == "T"]
    if not t_df.empty:
        t_df = t_df.set_index("date")
        st.line_chart(t_df["weight"])

    if st.button("A 화면으로 이동"):
        st.session_state["page"] = "A"

# -------------------- 메인 --------------------

def main():
    st.set_page_config(layout="wide")

    if "page" not in st.session_state:
        st.session_state["page"] = "A"

    if st.session_state["page"] == "A":
        page_A()
    else:
        page_B()

if __name__ == "__main__":
    main()


