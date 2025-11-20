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


def _normalize_calories_breakdown(cb_raw):
    """
    calories_breakdown을 항상 dict로, NaN 없이 정규화.
    {"아침": int, "점심": int, "저녁": int, "간식": int}
    """
    if not isinstance(cb_raw, dict):
        cb_raw = {}

    result = {}
    for k in ["아침", "점심", "저녁", "간식"]:
        v = cb_raw.get(k, 0)
        if pd.isna(v):
            v = 0
        try:
            v_int = int(v)
        except (TypeError, ValueError):
            v_int = 0
        result[k] = v_int
    return result


def _num_or_none(x):
    """숫자 컬럼용 NaN 정리."""
    if pd.isna(x):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _normalize_row_for_save(row: pd.Series) -> dict:
    """Supabase에 저장하기 전에 NaN/타입 정리."""
    payload = {
        "date": row["date"],
        "weight": _num_or_none(row.get("weight")),
        "status": row.get("status"),
        "calories_breakdown": _normalize_calories_breakdown(
            row.get("calories_breakdown")
        ),
        "total_calories": (
            int(row.get("total_calories"))
            if not pd.isna(row.get("total_calories"))
            and row.get("total_calories") is not None
            else None
        ),
        "score": _num_or_none(row.get("score")),
        "total_score": _num_or_none(row.get("total_score")),
    }
    return payload


def save_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    records 저장 로직 (전면 교체 버전)
    - delete() 사용 금지
    - id가 있는 row: update
    - id가 없는 row: insert 후 반환된 id를 df에 반영
    """
    if df is None or df.empty:
        return df

    supabase = get_supabase_client()
    df = df.copy()

    if "id" not in df.columns:
        df["id"] = None

    for idx, row in df.iterrows():
        row_id = row.get("id", None)
        # NaN id 처리
        if isinstance(row_id, float) and pd.isna(row_id):
            row_id = None

        payload = _normalize_row_for_save(row)

        if row_id is None:
            # 새 row → insert
            resp = (
                supabase.table("records")
                .insert(payload)
                .execute()
            )
            inserted = resp.data or []
            if inserted:
                new_id = inserted[0].get("id")
                df.at[idx, "id"] = new_id
        else:
            # 기존 row → update
            supabase.table("records").update(payload).eq("id", row_id).execute()

    return df


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
    """
    T와 T 사이의 빈 날짜를 F로 채워넣기.
    """
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
            "calories_breakdown": {
                "아침": 0,
                "점심": 0,
                "저녁": 0,
                "간식": 0,
            },
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

    # -------------------- 추가된 부분: 총합 포인트 항상 표시 --------------------
    if df.empty:
        st.write("총합 포인트: **0점**")
    else:
        last_total = df["total_score"].fillna(0).astype(float).iloc[-1]
        st.write(f"총합 포인트: **{last_total}점**")
    # ------------------------------------------------------------------------

    now_kst = get_kst_now()
    today_iso = now_kst.strftime("%Y-%m-%d")
    today_kr = now_kst.strftime("%Y년 %m월 %d일")

    # 오늘 기록 불러오기
    today_row = df[df["date"] == today_iso]

    if not today_row.empty:
        prev_cb = today_row.iloc[0]["calories_breakdown"]
        if not isinstance(prev_cb, dict):
            prev_cb = {"아침": 0, "점심": 0, "저녁": 0, "간식": 0}
        else:
            prev_cb = _normalize_calories_breakdown(prev_cb)
        prev_weight = today_row.iloc[0]["weight"]
    else:
        prev_cb = {"아침": 0, "점심": 0, "저녁": 0, "간식": 0}
        prev_weight = None

    st.subheader("오늘 날짜")
    st.write(f"한국 기준: **{today_kr}**")

    # 직전 T 찾기
    last_T = find_last_T(df, today_iso)
    if last_T is not None:
        last_t_date, last_t_weight = last_T
        st.info(f"이전 몸무게: {last_t_date} / {last_t_weight} kg")
    else:
        last_t_date, last_t_weight = None, None
        st.info("직전 T 없음 (첫 기록)")

        # ---------------- 오늘 T 몸무게 표시 ----------------
    if not today_row.empty:
        today_weight_display = today_row.iloc[0]["weight"]
        if today_weight_display is not None:
            st.info(f"오늘 몸무게: {today_iso} / {today_weight_display} kg")
    # ----------------------------------------------------------


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
        "아침": int(kcal_bf),
        "점심": int(kcal_lu),
        "저녁": int(kcal_di),
        "간식": int(kcal_sn),
    }

    # ---------- 식단만 저장 ----------
    if st.button("오늘 식단만 저장 (몸무게/포인트 X)"):
        if not today_row.empty:
            idxs = df.index[df["date"] == today_iso]
            df.loc[idxs, "calories_breakdown"] = [cb_dict] * len(idxs)
            df.loc[idxs, "total_calories"] = int(total_kcal)
        else:
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

        df = df.sort_values("date").reset_index(drop=True)
        save_data(df)
        st.success("오늘 식단만 저장되었습니다.")

    # ---------- T 기록 저장 ----------
    if st.button("오늘 T 기록 저장 (몸무게 인증)"):

        cutoff_date = (
            datetime.strptime(today_iso, "%Y-%m-%d") - timedelta(days=30)
        ).strftime("%Y-%m-%d")

        df_recent = df[df["date"] >= cutoff_date].copy()

        if not df.empty and (
            (df["date"] == today_iso) & (df["status"] == "T")
        ).any():
            st.error("오늘 날짜에 이미 T 기록이 존재합니다.")
            return

        existing_today_rows = df_recent[df_recent["date"] == today_iso]

        if last_t_date is not None:
            df_recent, num_f = mark_F(df_recent, last_t_date, today_iso)
        else:
            num_f = 0

        if not existing_today_rows.empty:
            idxs = df_recent.index[df_recent["date"] == today_iso]
            df_recent.loc[idxs, "weight"] = float(weight)
            df_recent.loc[idxs, "status"] = "T"
            df_recent.loc[idxs, "calories_breakdown"] = [cb_dict] * len(idxs)
            df_recent.loc[idxs, "total_calories"] = int(total_kcal)
        else:
            new_row = {
                "id": None,
                "date": today_iso,
                "weight": float(weight),
                "status": "T",
                "calories_breakdown": cb_dict,
                "total_calories": int(total_kcal),
                "score": 0.0,
                "total_score": 0.0,
            }
            df_recent = pd.concat([df_recent, pd.DataFrame([new_row])], ignore_index=True)

        t_only = df_recent[df_recent["status"] == "T"].sort_values("date")
        today_t_row = t_only[t_only["date"] == today_iso]
        if today_t_row.empty:
            st.error("내부 오류: 오늘 T row를 찾을 수 없습니다.")
            return

        today_t_idx = today_t_row.index[0]
        t_ordered_idxs = list(t_only.index)
        pos = t_ordered_idxs.index(today_t_idx)
        prev_pos = pos - 1

        if prev_pos >= 0:
            prev_weight_val = float(t_only.loc[t_ordered_idxs[prev_pos], "weight"])
        else:
            prev_weight_val = None

        today_score = calculate_score(prev_weight_val, float(weight), num_f)

        df_recent.loc[today_t_idx, "score"] = float(today_score)

        df_recent = df_recent.sort_values("date").reset_index(drop=True)
        df_recent = recalc_total_scores(df_recent)

        df_older = df[df["date"] < cutoff_date].copy()
        df_merged = pd.concat([df_older, df_recent], ignore_index=True)
        df_merged = df_merged.sort_values("date").reset_index(drop=True)

        df_merged = save_data(df_merged)

        st.success("오늘 T 기록이 저장되었습니다.")
        st.write(f"F 개수: {num_f}")
        st.write(f"오늘 포인트: **{today_score}점**")

        latest_total = (
            df_recent["total_score"].fillna(0).astype(float).iloc[-1]
            if not df_recent.empty
            else 0.0
        )
        st.write(f"총합 포인트: **{latest_total}점**")

    # ---------------- B 이동 ----------------
    if st.button("B 화면으로 이동"):
        st.session_state["page"] = "B"
        st.rerun()

    # ---------------- T 기록 수정 ----------------
    st.markdown("---")
    st.subheader("T 기록 수정하기")

    df = load_data()
    t_rows = df[df["status"] == "T"].sort_values("date")
    t_dates = t_rows["date"].tolist()

    if not t_dates:
        st.write("수정할 T 기록이 없습니다.")
        return

    selected_date = st.selectbox("수정할 날짜 선택", t_dates)

    row = t_rows[t_rows["date"] == selected_date].iloc[0]
    old_weight = row["weight"]
    old_cb = row["calories_breakdown"]
    if not isinstance(old_cb, dict):
        old_cb = {"아침": 0, "점심": 0, "저녁": 0, "간식": 0}
    else:
        old_cb = _normalize_calories_breakdown(old_cb)
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
        "아침": int(new_kcal_bf),
        "점심": int(new_kcal_lu),
        "저녁": int(new_kcal_di),
        "간식": int(new_kcal_sn),
    }

    if st.button("이 날짜 수정 저장"):

        df.loc[df["date"] == selected_date, "weight"] = float(new_weight)
        df.loc[df["date"] == selected_date, "calories_breakdown"] = new_cb_dict
        df.loc[df["date"] == selected_date, "total_calories"] = int(
            new_total_kcal
        )

        t_only = df[df["status"] == "T"].sort_values("date").copy()
        t_idxs = list(t_only.index)

        for pos, idx in enumerate(t_idxs):
            if pos == 0:
                prev_w = None
            else:
                prev_w = float(t_only.loc[t_idxs[pos - 1], "weight"])
            cur_w = float(t_only.loc[idx, "weight"])
            new_score = calculate_score(prev_w, cur_w, 0)
            df.loc[idx, "score"] = new_score

        df = df.sort_values("date").reset_index(drop=True)
        df = recalc_total_scores(df)

        df = save_data(df)
        st.success("수정 완료! 그래프와 기록이 업데이트되었습니다.")


# ---------------- B 화면 ----------------


def page_B():
    st.title("성진 다이어트 프로그램 – B 화면 (그래프/키)")

    df = load_data()
    config = load_config()

    st.subheader("키 설정")

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
