import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
import json

# ---------------- 기본 설정 ----------------

DATA_FILE = Path("data.csv")
CONFIG_FILE = Path("config.json")


# ---------------- 유틸 함수 ----------------

def get_kst_now():
    """현재 시간을 한국(KST)으로 반환."""
    return datetime.utcnow() + timedelta(hours=9)


def load_data():
    """몸무게/포인트 기록 불러오기."""
    if DATA_FILE.exists():
        return pd.read_csv(DATA_FILE)
    return pd.DataFrame(
        columns=[
            "date", "weight", "status",
            "calories_breakdown", "total_calories", "score"
        ]
    )


def save_data(df: pd.DataFrame):
    df.to_csv(DATA_FILE, index=False)


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(config: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def find_last_T(df: pd.DataFrame):
    """가장 최근 T 기록 반환. 없으면 None."""
    t_rows = df[df["status"] == "T"].copy()
    if t_rows.empty:
        return None
    t_rows = t_rows.sort_values("date")
    row = t_rows.iloc[-1]
    return row["date"], row["weight"]


def mark_F(df: pd.DataFrame, last_t_date: str, today_date: str):
    """T와 T 사이의 빈 날짜를 F로 채워넣기."""
    last = datetime.strptime(last_t_date, "%Y-%m-%d")
    today = datetime.strptime(today_date, "%Y-%m-%d")

    missing_days = (today - last).days - 1
    if missing_days <= 0:
        return df, 0

    existing_dates = set(df["date"].tolist())
    added_f = 0

    for i in range(1, missing_days + 1):
        f_date = (last + timedelta(days=i)).strftime("%Y-%m-%d")
        if f_date in existing_dates:
            continue
        row = {
            "date": f_date,
            "weight": None,
            "status": "F",
            "calories_breakdown": json.dumps({}),
            "total_calories": None,
            "score": 0.0
        }
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        added_f += 1

    return df, added_f


def calculate_score(prev_weight, today_weight, num_f):
    """포인트 계산."""
    score = 0.0

    if prev_weight is not None:
        diff = today_weight - prev_weight
        step = int(abs(diff) / 0.1)  # 0.1 단위 정확 계산
        if diff < 0:
            score += step * 0.2
        elif diff > 0:
            score -= step * 0.2

    score -= num_f * 0.3

    return round(score, 2)


# ---------------- A 화면 ----------------

def page_A():
    st.title("성진 다이어트 프로그램 – A 화면 (기록/포인트)")

    df = load_data()

    now_kst = get_kst_now()
    today_iso = now_kst.strftime("%Y-%m-%d")
    today_kr = now_kst.strftime("%Y년 %m월 %d일")

    st.subheader("오늘 날짜")
    st.write(f"한국 기준: **{today_kr}**")

    last_T = find_last_T(df)
    if last_T is not None:
        last_t_date, last_t_weight = last_T
        st.info(f"직전 T: {last_t_date} / {last_t_weight} kg")
    else:
        last_t_date, last_t_weight = None, None
        st.info("직전 T 없음 (첫 기록)")

    st.markdown("---")
    st.subheader("오늘 몸무게 / 식단 입력")

    weight = st.number_input(
        "오늘 몸무게 (kg)",
        min_value=30.0, max_value=300.0,
        step=0.1, format="%.1f"
    )

    col1, col2 = st.columns(2)

    with col1:
        bf = st.text_input("아침 식단")
        lu = st.text_input("점심 식단")
        di = st.text_input("저녁 식단")
        sn = st.text_input("간식")

    with col2:
        kcal_bf = st.number_input("아침 칼로리", min_value=0, step=10)
        kcal_lu = st.number_input("점심 칼로리", min_value=0, step=10)
        kcal_di = st.number_input("저녁 칼로리", min_value=0, step=10)
        kcal_sn = st.number_input("간식 칼로리", min_value=0, step=10)

    total_kcal = kcal_bf + kcal_lu + kcal_di + kcal_sn
    st.write(f"**총합 칼로리:** {total_kcal} kcal")

    if st.button("오늘 T 기록 저장"):

        # ---------------- 30일 초과 기록 자동 삭제 ----------------
        cutoff_date = (datetime.strptime(today_iso, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
        df = df[df["date"] >= cutoff_date].copy()
        # ------------------------------------------------------------

        # 중복 T 방지
        if not df.empty and ((df["date"] == today_iso) & (df["status"] == "T")).any():
            st.error("오늘 날짜에 이미 T 기록이 존재합니다.")
            return

        # F 처리
        if last_t_date is not None:
            df, num_f = mark_F(df, last_t_date, today_iso)
        else:
            num_f = 0

        today_score = calculate_score(last_t_weight, weight, num_f)

        cb_dict = {
            "아침": kcal_bf,
            "점심": kcal_lu,
            "저녁": kcal_di,
            "간식": kcal_sn
        }

        new_row = {
            "date": today_iso,
            "weight": weight,
            "status": "T",
            "calories_breakdown": json.dumps(cb_dict, ensure_ascii=False),
            "total_calories": total_kcal,
            "score": today_score
        }

        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        save_data(df)

        st.success("오늘 T 기록이 저장되었습니다.")
        st.write(f"F 개수: {num_f} → 패널티 {num_f * -0.3}점")
        if last_t_weight is not None:
            st.write(f"직전: **{last_t_weight} kg** → 오늘: **{weight} kg**")
        st.write(f"오늘 포인트: **{today_score}점**")

    if st.button("B 화면으로 이동"):
        st.session_state["page"] = "B"
        st.rerun()



    st.markdown("---")
    st.subheader("T 기록 수정하기")

    # T인 날짜 목록 가져오기
    t_rows = df[df["status"] == "T"].sort_values("date")
    t_dates = t_rows["date"].tolist()

    if not t_dates:
        st.write("수정할 T 기록이 없습니다.")
    else:
        selected_date = st.selectbox("수정할 날짜 선택", t_dates)

        # 선택한 날짜의 기존 데이터
        row = t_rows[t_rows["date"] == selected_date].iloc[0]
        old_weight = row["weight"]
        old_cb = json.loads(row["calories_breakdown"]) if row["calories_breakdown"] else {}
        old_total = row["total_calories"]

        st.write(f"기존 몸무게: **{old_weight} kg**")
        st.write(f"기존 총 칼로리: **{old_total} kcal**")

        # 몸무게 수정
        new_weight = st.number_input(
            "새 몸무게 입력", 
            min_value=30.0, max_value=300.0,
            step=0.1,
            value=float(old_weight)
        )

        # 칼로리 수정
        st.write("식단 칼로리 수정")
        new_kcal_bf = st.number_input("아침", min_value=0, step=10, value=int(old_cb.get("아침", 0)))
        new_kcal_lu = st.number_input("점심", min_value=0, step=10, value=int(old_cb.get("점심", 0)))
        new_kcal_di = st.number_input("저녁", min_value=0, step=10, value=int(old_cb.get("저녁", 0)))
        new_kcal_sn = st.number_input("간식", min_value=0, step=10, value=int(old_cb.get("간식", 0)))

        new_total_kcal = new_kcal_bf + new_kcal_lu + new_kcal_di + new_kcal_sn
        new_cb_dict = {
            "아침": new_kcal_bf,
            "점심": new_kcal_lu,
            "저녁": new_kcal_di,
            "간식": new_kcal_sn
        }

        if st.button("이 날짜 수정 저장"):
            # 수정된 기록을 DF에 반영
            df.loc[df["date"] == selected_date, "weight"] = new_weight
            df.loc[df["date"] == selected_date, "calories_breakdown"] = json.dumps(new_cb_dict, ensure_ascii=False)
            df.loc[df["date"] == selected_date, "total_calories"] = new_total_kcal

            # 포인트 재계산
            # 직전 T 찾기
            before_df = df[df["status"] == "T"].sort_values("date")
            idx = before_df[before_df["date"] == selected_date].index[0]
            prev_idx = before_df.index.get_loc(idx) - 1

            if prev_idx >= 0:
                prev_weight = float(before_df.iloc[prev_idx]["weight"])
            else:
                prev_weight = None

            # F 개수는 기존 그대로 (수정날짜의 F는 없음)
            df.loc[df["date"] == selected_date, "score"] = calculate_score(prev_weight, new_weight, 0)

            save_data(df)
            st.success("수정 완료! 그래프와 기록이 업데이트되었습니다.")



# ---------------- B 화면 ----------------

def page_B():
    st.title("성진 다이어트 프로그램 – B 화면 (그래프/키)")

    df = load_data()
    config = load_config()

    st.subheader("키 설정")

    current_height = config.get("height_cm", 170.0)
    height = st.number_input(
        "키 (cm)",
        min_value=100.0, max_value=250.0,
        step=0.1, value=float(current_height), format="%.1f"
    )

    if st.button("키 저장"):
        config["height_cm"] = float(height)
        save_config(config)
        st.success("키 저장 완료.")

    st.markdown("---")
    st.subheader("최근 30일 기록")

    if df.empty:
        st.write("기록 없음.")
        return

    df["date_dt"] = pd.to_datetime(df["date"])
    df = df.sort_values("date_dt").reset_index(drop=True)
    recent = df.tail(30).copy()

    display = recent.copy()
    display["weight_display"] = display["weight"].apply(lambda x: "-" if pd.isna(x) else x)

    st.dataframe(
        display[["date", "weight_display", "status", "total_calories", "score"]]
        .rename(columns={
            "date": "날짜", "weight_display": "몸무게",
            "status": "T/F", "total_calories": "칼로리", "score": "점수"
        }),
        use_container_width=True
    )

    st.subheader("체중 그래프 (T만 연결)")

    graph_df = recent[recent["status"] == "T"].copy()
    if graph_df.empty:
        st.write("그래프 표시할 T가 없습니다.")
        return

    graph_df["weight"] = graph_df["weight"].astype(float)
    graph_df = graph_df.set_index("date_dt")
    st.line_chart(graph_df["weight"])

    if st.button("A 화면으로 이동"):
        st.session_state["page"] = "A"
        st.rerun()



# ---------------- 메인 ----------------

def main():
    st.set_page_config(page_title="성진 다이어트 프로그램", layout="wide")

    if "page" not in st.session_state:
        st.session_state["page"] = "A"

    if st.session_state["page"] == "A":
        page_A()
    else:
        page_B()


if __name__ == "__main__":
    main()
