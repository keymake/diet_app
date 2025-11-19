import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

from supabase import create_client, Client


# ---------------- Supabase 설정 ----------------


@st.cache_resource
def get_supabase_client() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


# ---------------- 유틸 함수 ----------------


def get_kst_now():
    """현재 시간을 한국(KST)으로 반환."""
    return datetime.utcnow() + timedelta(hours=9)


def empty_data_df():
    """빈 데이터프레임 기본 스키마."""
    return pd.DataFrame(
        columns=[
            "id",
            "date",
            "weight",
            "status",
            "calories_breakdown",
            "total_calories",
            "score",
            "total_score",
        ]
    )


def load_data():
    """몸무게/포인트 기록을 Supabase에서 불러오기."""
    supabase = get_supabase_client()
    resp = supabase.table("records").select("*").execute()
    data = resp.data or []

    if not data:
        return empty_data_df()

    df = pd.DataFrame(data)

    # 누락 컬럼 자동 보정
    for col in [
        "id",
        "date",
        "weight",
        "status",
        "calories_breakdown",
        "total_calories",
        "score",
        "total_score",
    ]:
        if col not in df.columns:
            df[col] = None

    # 날짜 순 정렬
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _normalize_row_for_save(row: pd.Series) -> dict:
    """Supabase에 저장하기 전에 NaN/타입 정리."""
    def num_or_none(x):
        if pd.isna(x):
            return None
        return float(x)

    payload = {
        "date": row["date"],
        "weight": num_or_none(row.get("weight")),
        "status": row.get("status"),
        "calories_breakdown": row.get("calories_breakdown") or {},
        "total_calories": num_or_none(row.get("total_calories")),
        "score": num_or_none(row.get("score")),
        "total_score": num_or_none(row.get("total_score")),
    }
    return payload


def save_rows(df: pd.DataFrame, cutoff_date: str | None = None):
    """
    records 전체 삭제 안 하고,
    날짜(date) 기준으로 한 줄씩 insert/update만 한다.
    cutoff_date가 주어지면 그 이전 날짜 기록만 삭제.
    """
    supabase = get_supabase_client()

    if df.empty and cutoff_date is None:
        # 진짜 아무 것도 없는데 싹 비우는 행동은 하지 말자.
        return

    # 날짜별로 한 줄씩 upsert 비슷하게 처리
    for _, row in df.iterrows():
        payload = _normalize_row_for_save(row)

        # 해당 날짜 row가 이미 있는지 확인
        existing = (
            supabase.table("records")
            .select("id")
            .eq("date", payload["date"])
            .execute()
            .data
        )

        if existing:
            row_id = existing[0]["id"]
            supabase.table("records").update(payload).eq("id", row_id).execute()
        else:
            supabase.table("records").insert(payload).execute()

    # 30일 이전 것 정리
    if cutoff_date is not None:
        supabase.table("records").delete().lt("date", cutoff_date).execute()


def load_config():
    """설정(config)을 Supabase에서 불러오기."""
    supabase = get_supabase_client()
    resp = supabase.table("config").select("*").execute()
    rows = resp.data or []

    config = {}
    for row in rows:
        key = row.get("key")
        value = row.get("value")
        if key is None:
            continue
        config[key] = value
    return config


def save_config(config: dict):
    """설정을 Supabase에 저장 (전체 삭제 후 재삽입)."""
    # config는 어차피 몇 줄 안 되니까 기존 방식 그대로 둔다.
    supabase = get_supabase_client()
    supabase.table("config").delete().neq("id", 0).execute()

    rows = [{"key": k, "value": str(v)} for k, v in config.items()]
    if rows:
        supabase.table("config").insert(rows).execute()


def find_last_T(df: pd.DataFrame, today_iso: str):
    """
    오늘(today_iso)을 제외하고,
    가장 최근 T 기록 반환. 없으면 None.
    """
    t_rows = df[(df["status"] == "T") & (df["date"] < today_iso)].copy()
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
            "id": None,
            "date": f_date,
            "weight": None,
            "status": "F",
            "calories_breakdown": {},
            "total_calories": None,
            "score": 0.0,
            "total_score": None,
        }
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        added_f += 1

    return df, added_f


def calculate_score(prev_weight, today_weight, num_f):
    """포인트 계산."""
    score = 0.0

    if prev_weight is not None and today_weight is not None:
        diff = today_weight - prev_weight
        step = int(abs(diff) / 0.1)
        if diff < 0:
            score += step * 0.2
        elif diff > 0:
            score -= step * 0.2

    score -= num_f * 0.3

    return round(score, 2)


def recalc_total_scores(df: pd.DataFrame) -> pd.DataFrame:
    """total_score 전체 재계산"""
    total = 0.0
    for i in df.index:
        score = df.loc[i, "score"]
        if pd.isna(score):
            df.loc[i, "total_score"] = total
            continue
        total += float(score)
        df.loc[i, "total_score"] = round(total, 2)
    return df


# ---------------- A 화면 ----------------


def page_A():
    st.title("성진 다이어트 프로그램 – A 화면 (기록/포인트)")

    df = load_data()

    now_kst = get_kst_now()
    today_iso = now_kst.strftime("%Y-%m-%d")
    today_kr = now_kst.strftime("%Y년 %m월 %d일")

    # 오늘 기록 불러오기
    today_row = df[df["date"] == today_iso]

    if not today_row.empty:
        prev_cb = today_row.iloc[0]["calories_breakdown"] or {}
        if not isinstance(prev_cb, dict):
            prev_cb = {"아침": 0, "점심": 0, "저녁": 0, "간식": 0}
        prev_weight = today_row.iloc[0]["weight"]
    else:
        prev_cb = {"아침": 0, "점심": 0, "저녁": 0, "간식": 0}
        prev_weight = None

    st.subheader("오늘 날짜")
    st.write(f"한국 기준: **{today_kr}**")

    # 직전 T 찾기 (오늘 제외)
    last_T = find_last_T(df, today_iso)
    if last_T is not None:
        last_t_date, last_t_weight = last_T
        st.info(f"직전 T: {last_t_date} / {last_t_weight} kg")
    else:
        last_t_date, last_t_weight = None, None
        st.info("직전 T 없음 (첫 기록)")

    st.markdown("---")
    st.subheader("오늘 몸무게 / 식단 입력")

    weight = st.number_input(
        "오늘 몸무게 (kg, 저녁에 T 인증용)",
        min_value=30.0,
        max_value=300.0,
        step=0.1,
        value=float(prev_weight) if prev_weight else 60.0,
        format="%.1f",
    )

    col1, col2 = st.columns(2)

    with col1:
        bf = st.text_input("아침 식단")
        lu = st.text_input("점심 식단")
        di = st.text_input("저녁 식단")
        sn = st.text_input("간식")

    with col2:
        kcal_bf = st.number_input(
            "아침 칼로리",
            min_value=0,
            step=10,
            value=int(prev_cb.get("아침", 0)),
        )
        kcal_lu = st.number_input(
            "점심 칼로리",
            min_value=0,
            step=10,
            value=int(prev_cb.get("점심", 0)),
        )
        kcal_di = st.number_input(
            "저녁 칼로리",
            min_value=0,
            step=10,
            value=int(prev_cb.get("저녁", 0)),
        )
        kcal_sn = st.number_input(
            "간식 칼로리",
            min_value=0,
            step=10,
            value=int(prev_cb.get("간식", 0)),
        )

    total_kcal = kcal_bf + kcal_lu + kcal_di + kcal_sn
    st.write(f"**총합 칼로리:** {total_kcal} kcal")

    cb_dict = {
        "아침": kcal_bf,
        "점심": kcal_lu,
        "저녁": kcal_di,
        "간식": kcal_sn,
    }

    # ---------- 1) 식단만 저장 (몸무게/포인트는 손대지 않음) ----------
    if st.button("오늘 식단만 저장 (몸무게/포인트 X)"):
        # 오늘 row가 있으면 그 행만 갱신, 없으면 미확정 상태로 새로 생성
        if not today_row.empty:
            df.loc[df["date"] == today_iso, "calories_breakdown"] = cb_dict
            df.loc[df["date"] == today_iso, "total_calories"] = int(total_kcal)
            # status / score / total_score / weight는 그대로 둔다
        else:
            # 이전까지의 총합 포인트 유지
            if df.empty:
                prev_total_score = 0.0
            else:
                prev_total_score = (
                    df["total_score"].fillna(0).astype(float).iloc[-1]
                )

            new_row = {
                "id": None,
                "date": today_iso,
                "weight": None,
                "status": "미확정",
                "calories_breakdown": cb_dict,
                "total_calories": int(total_kcal),
                "score": 0.0,
                "total_score": float(prev_total_score),
            }
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

        save_rows(df)  # cutoff 없음, 삭제 없음
        st.success("오늘 식단만 저장되었습니다. (몸무게/포인트는 그대로)")

    # ---------- 2) 오늘 T 기록 저장 (몸무게 인증 & 포인트 반영) ----------
    if st.button("오늘 T 기록 저장 (몸무게 인증)"):
        # 30일 초과 기록 삭제용 기준
        cutoff_date = (
            datetime.strptime(today_iso, "%Y-%m-%d") - timedelta(days=30)
        ).strftime("%Y-%m-%d")

        # 30일 초과 기록 제거
        df = df[df["date"] >= cutoff_date].copy()

        # 중복 방지: 오늘 이미 T면 막기
        if not df.empty and (
            (df["date"] == today_iso) & (df["status"] == "T")
        ).any():
            st.error("오늘 날짜에 이미 T 기록이 존재합니다.")
            return

        # 오늘 기존 row(미확정/기타) 있으면 삭제 후 새로 넣는다
        df = df[df["date"] != today_iso].copy()

        # F 채우기 (직전 T 기준, 오늘 제외한 값 사용)
        if last_t_date is not None:
            df, num_f = mark_F(df, last_t_date, today_iso)
        else:
            num_f = 0

        today_score = calculate_score(last_t_weight, weight, num_f)

        # total_score 계산 (가장 마지막 total_score + today_score)
        if df.empty:
            prev_total_score = 0.0
        else:
            prev_total_score = (
                df["total_score"].fillna(0).astype(float).iloc[-1]
            )

        today_total_score = round(prev_total_score + today_score, 2)

        new_row = {
            "id": None,
            "date": today_iso,
            "weight": float(weight),
            "status": "T",
            "calories_breakdown": cb_dict,
            "total_calories": int(total_kcal),
            "score": float(today_score),
            "total_score": float(today_total_score),
        }

        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        df = df.sort_values("date").reset_index(drop=True)

        save_rows(df, cutoff_date=cutoff_date)

        st.success("오늘 T 기록이 저장되었습니다.")
        st.write(f"F 개수: {num_f}")
        st.write(f"오늘 포인트: **{today_score}점**")
        st.write(f"총합 포인트: **{today_total_score}점**")

    # ---------------- B 이동 ----------------
    if st.button("B 화면으로 이동"):
        st.session_state["page"] = "B"
        st.rerun()

    # ---------------- T 기록 수정 ----------------
    st.markdown("---")
    st.subheader("T 기록 수정하기")

    df = load_data()  # 위에서 저장했을 수 있으니 다시 로드
    t_rows = df[df["status"] == "T"].sort_values("date")
    t_dates = t_rows["date"].tolist()

    if not t_dates:
        st.write("수정할 T 기록이 없습니다.")
        return

    selected_date = st.selectbox("수정할 날짜 선택", t_dates)

    row = t_rows[t_rows["date"] == selected_date].iloc[0]
    old_weight = row["weight"]
    old_cb = row["calories_breakdown"] or {}
    if not isinstance(old_cb, dict):
        old_cb = {"아침": 0, "점심": 0, "저녁": 0, "간식": 0}
    old_total = row["total_calories"]

    st.write(f"기존 몸무게: **{old_weight} kg**")
    st.write(f"기존 총 칼로리: **{old_total} kcal**")

    new_weight = st.number_input(
        "새 몸무게 입력",
        min_value=30.0,
        max_value=300.0,
        step=0.1,
        value=float(old_weight),
        key="edit_weight",
    )

    st.write("식단 칼로리 수정")
    new_kcal_bf = st.number_input(
        "아침", min_value=0, step=10, value=int(old_cb.get("아침", 0)), key="edit_bf"
    )
    new_kcal_lu = st.number_input(
        "점심", min_value=0, step=10, value=int(old_cb.get("점심", 0)), key="edit_lu"
    )
    new_kcal_di = st.number_input(
        "저녁", min_value=0, step=10, value=int(old_cb.get("저녁", 0)), key="edit_di"
    )
    new_kcal_sn = st.number_input(
        "간식", min_value=0, step=10, value=int(old_cb.get("간식", 0)), key="edit_sn"
    )

    new_total_kcal = new_kcal_bf + new_kcal_lu + new_kcal_di + new_kcal_sn
    new_cb_dict = {
        "아침": new_kcal_bf,
        "점심": new_kcal_lu,
        "저녁": new_kcal_di,
        "간식": new_kcal_sn,
    }

    if st.button("이 날짜 수정 저장"):
        # 선택 날짜 행만 수정
        df.loc[df["date"] == selected_date, "weight"] = float(new_weight)
        df.loc[df["date"] == selected_date, "calories_breakdown"] = new_cb_dict
        df.loc[df["date"] == selected_date, "total_calories"] = int(
            new_total_kcal
        )

        # 점수 재계산: 바로 직전 T와의 차이만 반영
        before_df = df[df["status"] == "T"].sort_values("date")
        idx = before_df[before_df["date"] == selected_date].index[0]
        pos = list(before_df.index).index(idx)
        prev_pos = pos - 1

        if prev_pos >= 0:
            prev_weight_val = float(before_df.iloc[prev_pos]["weight"])
        else:
            prev_weight_val = None

        df.loc[df["date"] == selected_date, "score"] = calculate_score(
            prev_weight_val, float(new_weight), 0
        )

        # total_score 전체 재계산
        df = df.sort_values("date").reset_index(drop=True)
        df = recalc_total_scores(df)

        save_rows(df)  # 전체 삭제 없이 행별 update/insert
        st.success("수정 완료! 그래프와 기록이 업데이트되었습니다.")


# ---------------- B 화면 ----------------


def page_B():
    st.title("성진 다이어트 프로그램 – B 화면 (그래프/키)")

    df = load_data()
    config = load_config()

    st.subheader("키 설정")

    # 문자열로 저장된 값을 float로 변환
    current_height_raw = config.get("height_cm", "170.0")
    try:
        current_height = float(current_height_raw)
    except (TypeError, ValueError):
        current_height = 170.0

    height = st.number_input(
        "키 (cm)",
        min_value=100.0,
        max_value=250.0,
        step=0.1,
        value=float(current_height),
        format="%.1f",
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
    display["weight_display"] = display["weight"].apply(
        lambda x: "-" if pd.isna(x) else x
    )

    st.dataframe(
        display[
            [
                "date",
                "weight_display",
                "status",
                "total_calories",
                "score",
                "total_score",
            ]
        ].rename(
            columns={
                "date": "날짜",
                "weight_display": "몸무게",
                "status": "T/F",
                "total_calories": "칼로리",
                "score": "오늘점수",
                "total_score": "총합점수",
            }
        ),
        use_container_width=True,
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
    st.set_page_config(
        page_title="성진 다이어트 프로그램", layout="wide"
    )

    if "page" not in st.session_state:
        st.session_state["page"] = "A"

    if st.session_state["page"] == "A":
        page_A()
    else:
        page_B()


if __name__ == "__main__":
    main()
