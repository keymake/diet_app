import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import json
from supabase import create_client, Client

# --------------------------------------
# 기본 설정
# --------------------------------------
st.set_page_config(page_title="성진 다이어트 프로그램", layout="wide")

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_ANON_KEY"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

TABLE = "diet_records"
CONFIG_FILE = "config.json"


# --------------------------------------
# 유틸
# --------------------------------------
def get_kst_now():
    return datetime.utcnow() + timedelta(hours=9)


# 키 저장 config.json
def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# --------------------------------------
# Supabase 관련 함수
# --------------------------------------
def get_record(date_str):
    res = (
        supabase.table(TABLE)
        .select("*")
        .eq("date", date_str)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def get_last_T(before_date):
    q = supabase.table(TABLE).select("*").eq("status", "T").lt("date", before_date)
    res = q.order("date", desc=True).limit(1).execute()
    rows = res.data or []
    return rows[0] if rows else None


def get_last_total_score():
    """가장 최근 날짜의 total_score 반환"""
    res = (
        supabase.table(TABLE)
        .select("total_score")
        .order("date", desc=True)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        return 0.0
    return rows[0].get("total_score", 0.0)


def insert_record(date_str, weight, status, cal_dict, total_kcal, today_score, total_score):
    supabase.table(TABLE).insert({
        "date": date_str,
        "weight": weight,
        "status": status,
        "calories": {**cal_dict, "total": total_kcal},
        "score": today_score,
        "total_score": total_score
    }).execute()


def update_record(date_str, weight, status, cal_dict, total_kcal, today_score, total_score):
    supabase.table(TABLE).update({
        "weight": weight,
        "status": status,
        "calories": {**cal_dict, "total": total_kcal},
        "score": today_score,
        "total_score": total_score
    }).eq("date", date_str).execute()


def fill_missing_F(last_t_date, today_date):
    """T~T 사이 공백 날짜를 F로 채움"""
    last = datetime.strptime(last_t_date, "%Y-%m-%d").date()
    today = datetime.strptime(today_date, "%Y-%m-%d").date()

    gap = (today - last).days - 1
    if gap <= 0:
        return 0

    cnt = 0
    for i in range(1, gap + 1):
        d = (last + timedelta(days=i)).strftime("%Y-%m-%d")
        if not get_record(d):
            supabase.table(TABLE).insert({
                "date": d,
                "weight": None,
                "status": "F",
                "calories": None,
                "score": 0.0,
                "total_score": get_last_total_score()  # F에는 누적 점수 변화 없음
            }).execute()
            cnt += 1

    return cnt


def fetch_recent_30():
    res = (
        supabase.table(TABLE)
        .select("*")
        .order("date", asc=True)
        .execute()
    )
    rows = res.data or []
    if not rows:
        return pd.DataFrame(columns=["date", "weight", "status", "score", "total_score"])

    df = pd.DataFrame(rows)
    df["date_dt"] = pd.to_datetime(df["date"])
    cutoff = datetime.now().date() - timedelta(days=30)
    df = df[df["date_dt"].dt.date >= cutoff].copy()

    df["weight"] = pd.to_numeric(df["weight"], errors="coerce")
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    df["total_score"] = pd.to_numeric(df["total_score"], errors="coerce")

    return df


def calculate_score(prev_w, today_w, f_count):
    score = 0.0
    if prev_w is not None and today_w is not None:
        diff = today_w - prev_w
        step = int(abs(diff) / 0.1)
        if diff < 0:
            score += step * 0.2
        elif diff > 0:
            score -= step * 0.2
    score -= f_count * 0.3
    return round(score, 2)


# --------------------------------------
# A 화면
# --------------------------------------
def page_A():
    st.title("성진 다이어트 프로그램 – A 화면")

    now = get_kst_now()
    today = now.strftime("%Y-%m-%d")
    st.write(f"오늘 날짜: **{now.strftime('%Y년 %m월 %d일')}**")

    today_rec = get_record(today)
    last_T = get_last_T(today)

    if last_T:
        st.info(f"직전 T: {last_T['date']} / {last_T.get('weight', '-')} kg")
        prev_weight = last_T.get("weight")
        prev_t_date = last_T["date"]
    else:
        st.info("직전 T 없음")
        prev_weight = None
        prev_t_date = None

    st.markdown("---")

    # 입력 기본값
    default_w = today_rec.get("weight") if today_rec else 0.0
    cal_json = today_rec.get("calories") if today_rec else {}

    weight = st.number_input("오늘 몸무게(kg)", value=float(default_w), step=0.1)

    col1, col2 = st.columns(2)
    with col1:
        bf = st.text_input("아침", cal_json.get("아침", ""))
        lu = st.text_input("점심", cal_json.get("점심", ""))
        di = st.text_input("저녁", cal_json.get("저녁", ""))
        sn = st.text_input("간식", cal_json.get("간식", ""))
    with col2:
        kcal_bf = st.number_input("아침 kcal", value=int(cal_json.get("kcal_bf", 0)), step=10)
        kcal_lu = st.number_input("점심 kcal", value=int(cal_json.get("kcal_lu", 0)), step=10)
        kcal_di = st.number_input("저녁 kcal", value=int(cal_json.get("kcal_di", 0)), step=10)
        kcal_sn = st.number_input("간식 kcal", value=int(cal_json.get("kcal_sn", 0)), step=10)

    total_kcal = kcal_bf + kcal_lu + kcal_di + kcal_sn

    cal_dict = {
        "아침": bf, "점심": lu, "저녁": di, "간식": sn,
        "kcal_bf": kcal_bf, "kcal_lu": kcal_lu, "kcal_di": kcal_di, "kcal_sn": kcal_sn
    }

    if st.button("오늘 T 기록 저장"):
        if today_rec and today_rec.get("status") == "T":
            st.error("이미 오늘 T 기록 있음")
            return

        # F 채우기
        if prev_t_date:
            f_count = fill_missing_F(prev_t_date, today)
        else:
            f_count = 0

        # 오늘 점수
        today_score = calculate_score(prev_weight, weight, f_count)

        # 총합 점수
        last_total = get_last_total_score()
        new_total = round(last_total + today_score, 2)

        # 저장
        if today_rec:
            update_record(today, weight, "T", cal_dict, total_kcal, today_score, new_total)
        else:
            insert_record(today, weight, "T", cal_dict, total_kcal, today_score, new_total)

        st.success("저장 완료!")
        st.write(f"오늘 점수: **{today_score}점**")
        st.write(f"누적 총합: **{new_total}점**")

    if st.button("B 화면으로 이동"):
        st.session_state["page"] = "B"


# --------------------------------------
# B 화면
# --------------------------------------
def page_B():
    st.title("성진 다이어트 프로그램 – B 화면")

    # 키 저장
    cfg = load_config()
    height = st.number_input("키(cm)", value=float(cfg.get("height_cm", 170.0)), step=0.1)
    if st.button("키 저장"):
        cfg["height_cm"] = float(height)
        save_config(cfg)
        st.success("저장됨")

    df = fetch_recent_30()
    st.subheader("최근 30일 기록")

    if df.empty:
        st.write("기록 없음")
    else:
        df_display = df.copy()
        df_display["몸무게"] = df_display["weight"].apply(lambda x: "-" if pd.isna(x) else x)
        df_display["총칼로리"] = df_display["calories"].apply(lambda c: c.get("total") if isinstance(c, dict) else None)

        st.dataframe(
            df_display[["date", "몸무게", "status", "총칼로리", "score", "total_score"]]
            .rename(columns={"date": "날짜", "status": "T/F", "score": "오늘점수", "total_score": "누적점수"}),
            use_container_width=True
        )

        # 그래프
        t_df = df[df["status"] == "T"].copy()
        if not t_df.empty:
            t_df = t_df.set_index("date_dt")
            st.subheader("체중 그래프 (T만)")
            st.line_chart(t_df["weight"])

    if st.button("A 화면으로 이동"):
        st.session_state["page"] = "A"


# --------------------------------------
# Main
# --------------------------------------
def main():
    if "page" not in st.session_state:
        st.session_state["page"] = "A"

    if st.session_state["page"] == "A":
        page_A()
    else:
        page_B()


if __name__ == "__main__":
    main()
