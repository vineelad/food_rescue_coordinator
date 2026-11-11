"""Second Serving: local Streamlit coordinator for fictional SF food rescues."""

import base64
import json
import os
from copy import deepcopy
from datetime import time
from html import escape
from pathlib import Path

import pydeck as pdk
import streamlit as st
from dotenv import load_dotenv

from food_rescue.dashboard import decide_rescue, load_rescue, recent_rescues, start_rescue
from food_rescue.evaluation import run_case, run_langsmith, score
from food_rescue.scenarios import scenarios
from food_rescue.simulation import NEIGHBORHOODS, by_id, make_world

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
st.set_page_config(page_title="Second Serving · SF Food Rescue", page_icon="🍊", layout="wide")


def clock(minutes):
    if minutes is None:
        return "Not specified"
    hour, minute = divmod(minutes, 60)
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def notice_error(exc):
    # Provider exceptions can include request details; keep them out of the interface.
    if isinstance(exc, ValueError):
        st.error(str(exc))
    else:
        st.error("The request could not finish. Your saved rescue is still available. Check the service connection and try again.")


@st.cache_data
def hero_image():
    return base64.b64encode((ROOT / "assets/sf-food-rescue-hero.png").read_bytes()).decode()


def styling():
    st.html("""<style>
    .stApp { background: #f7f5ef; }
    .block-container { max-width: 1440px; padding-top: 1.5rem; padding-bottom: 3rem; }
    h1,h2,h3 { letter-spacing: -.035em; color: #203d39; }
    h1,h2 { font-family: Georgia, 'Times New Roman', serif !important; }
    [data-testid="stSidebar"] { background: #edeee5; border-right: 1px solid #dbe0d2; }
    [data-testid="stMetric"] { background: #fffdf8; border: 1px solid #e0e3d7; padding: 17px 20px; border-radius: 14px; }
    [data-testid="stMetricValue"] { font-family: Georgia, serif; color: #224c46; }
    [data-testid="stBaseButton-primary"] { background: #c65d38; border-color: #c65d38; border-radius: 9px; }
    [data-testid="stBaseButton-secondary"] { border-radius: 9px; }
    [data-testid="stVerticalBlockBorderWrapper"] { border-radius: 14px; }
    .masthead { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px; }
    .wordmark { font-family:Georgia,serif; font-size:27px; font-weight:700; letter-spacing:-1px; }
    .wordmark span { color:#c65d38; }
    .tag { font-size:10px; font-weight:700; letter-spacing:1.7px; border:1px solid #bdc8b9; border-radius:30px; padding:7px 11px; }
    .hero { height:290px; border-radius:18px; overflow:hidden; position:relative; margin-bottom:24px; background:#244b43; }
    .hero img { height:100%; width:100%; object-fit:cover; object-position:center; }
    .hero:after { content:''; position:absolute; inset:0; background:linear-gradient(90deg,rgba(23,52,46,.91),rgba(23,52,46,.55) 36%,transparent 70%); }
    .hero-copy { position:absolute; z-index:1; left:36px; top:29px; max-width:425px; color:#fffaf0; }
    .hero-copy h1 { font-size:48px; line-height:1.04; color:#fffaf0; margin:12px 0 12px; font-weight:500; }
    .hero-copy p { font-size:14px; max-width:330px; line-height:1.6; color:#f4f1e6; }
    .eyebrow { font-size:10px; font-weight:700; letter-spacing:2.4px; text-transform:uppercase; }
    .route-card { background:#fffdf8; border:1px solid #e0e3d7; border-radius:13px; padding:18px 20px; margin-bottom:12px; }
    .route-head { display:flex; justify-content:space-between; gap:12px; align-items:center; margin-bottom:12px; }
    .meal-chip { border-radius:30px; background:#e5eddf; color:#315a41; padding:5px 10px; font-size:12px; font-weight:700; white-space:nowrap; }
    .route-title { font-size:16px; font-weight:650; color:#244b43; }
    .route-detail { font-size:13px; color:#60736d; line-height:1.7; }
    .route-times { border-top:1px solid #e6e8dd; margin-top:12px; padding-top:11px; font-size:13px; color:#3e5650; }
    .stepbar { background:#e8ede2; padding:13px 18px; border-radius:11px; font-size:13px; margin:16px 0; color:#37584d; }
    .small-note { color:#748079; font-size:12px; margin:20px 0; }
    @media(max-width:700px) { .hero {height:310px;} .hero-copy {left:22px;right:22px;} .hero-copy h1 {font-size:39px;} .tag{font-size:8px;} }
    </style>""")


def masthead():
    st.html(f"""<div class="masthead"><div class="wordmark">second serving<span>↗</span></div>
    <div class="tag">SAN FRANCISCO · CLASSROOM SIMULATION</div></div>
    <div class="hero"><img src="data:image/png;base64,{hero_image()}" alt="San Francisco volunteers sharing fresh food beside an orange van, with the Golden Gate Bridge behind them">
    <div class="hero-copy"><div class="eyebrow">Good food deserves another stop.</div>
    <h1>A little surplus.<br>A lot of possibility.</h1>
    <p>Connect neighborhood kitchens with neighbors who need a meal. One thoughtful pickup at a time.</p></div></div>""")


def set_active(rescue):
    st.session_state.active_thread = rescue["thread_id"]
    st.session_state.rescue = rescue
    st.query_params["rescue"] = rescue["thread_id"]


def sidebar():
    with st.sidebar:
        st.markdown("### Your dispatch desk")
        st.caption("NEIGHBORS HELPING NEIGHBORS")
        st.write("A small local network. A shared table.")
        st.divider()
        agent_ready = bool(os.getenv("NEBIUS_API_KEY") and os.getenv("NEBIUS_MODEL"))
        st.selectbox("Planner", ["Nebius agent", "Nearest-shelter baseline"],
                     index=0 if agent_ready else 1, key="planner_choice")
        if not agent_ready:
            st.caption("The baseline is ready. Add Nebius credentials in .env to use the agent.")
        st.caption("Agent runs use your configured Nebius model. They start only when you request a plan.")
        st.divider()
        st.markdown("**Continue a saved rescue**")
        saved = recent_rescues()
        labels = {r["thread_id"]: f"{r['state']['donation']['quantity']} meals · {r['state']['status'].replace('_', ' ')} · {r['thread_id'][:6]}" for r in saved}
        chosen = st.selectbox("Recent rescues", [""] + list(labels),
                              format_func=lambda k: labels.get(k, "Choose a saved rescue"), key="saved_choice")
        if st.button("Open saved rescue", disabled=not chosen, width="stretch"):
            set_active(load_rescue(chosen))
            st.rerun()
        with st.expander("Open with a thread ID"):
            thread_id = st.text_input("Thread ID", placeholder="Paste a CLI or app thread ID")
            if st.button("Load rescue", disabled=not thread_id):
                try:
                    set_active(load_rescue(thread_id.strip()))
                    st.rerun()
                except Exception as exc:
                    notice_error(exc)
        st.divider()
        st.caption("10 kitchens · 6 shelters · 5 drivers")
        st.caption("Mission / SoMa / Tenderloin / Chinatown / Bayview")
        st.caption("All participants and logistics are fictional. Schedules use Pacific time.")


def intake():
    st.markdown("### Start with something good")
    st.caption("Choose a classroom scenario, or make it your own.")
    cases = {c["name"]: c for c in scenarios()}
    names = list(cases)
    selected = st.selectbox("Scenario", names, index=names.index("split_donation"),
                            format_func=lambda n: n.replace("_", " ").title(), key="scenario_choice")
    case = cases[selected]
    inputs = deepcopy(case["inputs"])
    with st.form("new_rescue"):
        custom = st.checkbox("Customize this donation", key="customize")
        quantity = st.number_input("Boxed meals", min_value=1, max_value=500, value=inputs["donation"]["quantity"], step=5)
        restaurant = st.selectbox("Pickup kitchen", inputs["world"]["restaurants"],
                                  format_func=lambda r: r["name"])
        food_type = st.selectbox("Food type", ["vegetarian", "vegan", "mixed"])
        one, two = st.columns(2)
        pickup_start = one.time_input("Pickup from", value=time(15, 0))
        pickup_end = two.time_input("Pickup until", value=time(16, 0))
        st.caption("Custom fields apply only when customization is checked. Scenario constraints remain in place.")
        submitted = st.form_submit_button("Find a rescue plan →", type="primary", width="stretch")
    if submitted:
        if st.session_state.planner_choice == "Nebius agent" and not (os.getenv("NEBIUS_API_KEY") and os.getenv("NEBIUS_MODEL")):
            st.error("Set NEBIUS_API_KEY and NEBIUS_MODEL in .env, or choose the baseline planner.")
            return
        if custom:
            start, end = pickup_start.hour * 60 + pickup_start.minute, pickup_end.hour * 60 + pickup_end.minute
            if end <= start:
                st.error("The pickup window must end after it starts.")
                return
            inputs["donation"].update(quantity=quantity, restaurant_id=restaurant["id"],
                                      food_type=food_type, pickup_start=start, pickup_end=end)
        try:
            with st.spinner("Checking recipients, transport, and pickup windows. Agent planning can take a minute or two…"):
                rescue = start_rescue(inputs, "agent" if st.session_state.planner_choice == "Nebius agent" else "baseline")
            set_active(rescue)
            st.rerun()
        except Exception as exc:
            notice_error(exc)


def neighborhood_map(world, donation=None, plan=None):
    points, routes = [], []
    origin = by_id(world["restaurants"], donation["restaurant_id"])["neighborhood"] if donation else None
    for name, (lat, lon) in NEIGHBORHOODS.items():
        points.append({"position": [lon, lat], "name": name, "color": [200, 96, 61] if name == origin else [53, 105, 95]})
    for leg in (plan or {}).get("legs", []):
        shelter = by_id(world["shelters"], leg["shelter_id"])
        a, b = NEIGHBORHOODS[origin], NEIGHBORHOODS[shelter["neighborhood"]]
        routes.append({"source": [a[1], a[0]], "target": [b[1], b[0]],
                       "name": f"{leg['quantity']} meals → {shelter['name']}"})
    layers = [pdk.Layer("ArcLayer", routes, get_source_position="source", get_target_position="target",
                         get_source_color=[200, 96, 61], get_target_color=[53, 105, 95], get_width=4, pickable=True),
              pdk.Layer("ScatterplotLayer", points, get_position="position", get_fill_color="color",
                        get_radius=170, pickable=True),
              pdk.Layer("TextLayer", points, get_position="position", get_text="name", get_size=13,
                        get_color=[32, 61, 57], get_pixel_offset=[0, -20])]
    st.pydeck_chart(pdk.Deck(layers=layers, map_provider="carto", map_style="light",
                            initial_view_state=pdk.ViewState(latitude=37.765, longitude=-122.415, zoom=11.5, pitch=0),
                            tooltip={"text": "{name}"}), height=340, width="stretch")
    st.caption("Neighborhood-level illustration, not street directions. Travel times and locations are simulated. Basemap requires internet.")


def rescue_board(rescue):
    state, thread_id = rescue["state"], rescue["thread_id"]
    world, donation, plan = state["world"], state["donation"], state["plan"]
    allocated = sum(l["quantity"] for l in plan.get("legs", []))
    booked = sum(r["quantity"] for r in world["reservations"] if r["donation_id"] == donation["id"])
    st.markdown("### A good meal, with a plan")
    status = state["status"]
    if rescue["pending_approval"]:
        st.info("Your review is the last step. Check the plan below, then approve the simulated pickups.")
    elif status == "scheduled":
        st.success(f"Pickups confirmed. {booked} meals are reserved for this rescue.")
    elif status == "rejected":
        st.warning("You declined this plan. No pickups were reserved for it.")
    elif status == "awaiting_confirmation":
        st.warning("A recipient or driver has not confirmed yet. This plan cannot be approved until they confirm.")
    elif status == "needs_information":
        st.warning("More donation details are needed. Start a customized rescue with the missing information.")
    elif status == "no_feasible_plan":
        st.warning("There is no feasible pickup with the current constraints. A coordinator needs to help.")
    else:
        st.error("This plan needs attention before it can be scheduled.")
    if state.get("event_applied"):
        if rescue["pending_approval"]:
            st.warning("A driver cancelled. The earlier reservations were released. This revised plan needs a fresh decision.")
        else:
            st.caption("A driver cancelled earlier. This rescue reflects the revised plan.")
    restaurant = by_id(world["restaurants"], donation["restaurant_id"])
    st.markdown(f"**{restaurant['name']}** · {donation['food_type'].title()} · {clock(donation['pickup_start'])}–{clock(donation['pickup_end'])}")
    st.html('<div class="stepbar">① Donation received &nbsp; → &nbsp; ② Plan checked &nbsp; → &nbsp; ③ Coordinator approval &nbsp; → &nbsp; ④ Pickups scheduled</div>')
    for index, leg in enumerate(plan.get("legs", []), 1):
        shelter = by_id(world["shelters"], leg["shelter_id"])
        driver = by_id(world["drivers"], leg["driver_id"])
        confirmation = "Driver and recipient confirmed" if driver["confirmed"] and shelter["confirmed"] else "Participant confirmation pending"
        st.html(f'''<div class="route-card"><div class="route-head"><div class="route-title">{index:02d} &nbsp; {escape(shelter['name'])}</div><div class="meal-chip">{leg['quantity']} meals</div></div>
        <div class="route-detail">{escape(restaurant['neighborhood'])} → {escape(shelter['neighborhood'])}<br>{escape(driver['name'])} · {confirmation}</div>
        <div class="route-times">Pickup <strong>{clock(leg['pickup_at'])}</strong> &nbsp; → &nbsp; Arrive <strong>{clock(leg['deliver_at'])}</strong> · Pacific</div></div>''')
    if allocated < donation["quantity"] and plan.get("legs"):
        st.warning(f"{donation['quantity'] - allocated} meals remain unallocated. Approval covers only the listed meals.")
    if rescue["pending_approval"]:
        approve, reject = st.columns([2, 1])
        decision = None
        if approve.button("Approve pickups ✓", type="primary", width="stretch", key="approve_pickups"):
            decision = True
        if reject.button("Decline plan", width="stretch", key="decline_plan"):
            decision = False
        if decision is not None:
            try:
                with st.spinner("Saving your decision and checking for changes…"):
                    set_active(decide_rescue(thread_id, decision, rescue["checkpoint_id"]))
                st.rerun()
            except Exception as exc:
                notice_error(exc)
    if st.button("Refresh saved plan", key="refresh_plan"):
        set_active(load_rescue(thread_id))
        st.rerun()
    st.caption(f"Rescue reference: {thread_id}")
    st.download_button("Download rescue plan", json.dumps(state, indent=2),
                       file_name=f"rescue-{thread_id[:8]}.json", mime="application/json")


def activity(rescue):
    st.markdown("### Every handoff has a story")
    if not rescue:
        st.info("Start a rescue to see the agent's tool calls and planning history.")
        return
    history = rescue["state"].get("history", [])
    for i, entry in enumerate(history, 1):
        event = entry["event"]
        with st.expander(f"{i:02d} · {event.replace('_', ' ').title()}", expanded=event == "plan"):
            if event == "plan":
                st.write(entry["plan"].get("explanation", ""))
                tools = entry["plan"].get("trajectory", [])
                if tools:
                    st.caption(f"{len(tools)} tool calls · {entry['plan'].get('total_tokens', 0):,} reported tokens")
                    for call in tools:
                        st.code(json.dumps(call, indent=2), language="json")
                else:
                    st.caption("This plan used the deterministic baseline.")
            else:
                st.json(entry)
    st.link_button("Open LangSmith", "https://smith.langchain.com")


def evaluations():
    st.markdown("### Measure the difference")
    st.write("Compare a nearest-shelter rule with the agent on repeatable classroom scenarios.")
    st.caption("Evaluations simulate coordinator approval. Running them does not approve the rescue on your dispatch board.")
    a, b, c = st.columns([2, 1, 2])
    planner = a.selectbox("Evaluate", ["baseline", "agent"], key="eval_planner")
    split = b.selectbox("Dataset split", ["dev", "test"], key="eval_split")
    hosted = c.checkbox("Upload experiment to LangSmith", value=False, key="eval_hosted")
    st.caption("Agent evaluations make paid Nebius calls and may take several minutes. Start with the baseline.")
    if st.button("Run evaluation", type="primary", key="run_eval"):
        try:
            with st.spinner("Running independent scenarios…"):
                if hosted:
                    results = run_langsmith(planner, split)
                    from langsmith import Client
                    project = Client().read_project(project_id=results.experiment_id)
                    st.session_state.hosted_experiment_url = project.url or "https://smith.langchain.com"
                else:
                    rows = []
                    for case in scenarios():
                        if case["split"] == split:
                            output = run_case(case["inputs"], planner)
                            rows.append({"scenario": case["name"], **score(case["inputs"], output, case["outputs"])})
                    st.session_state.eval_rows = rows
                    st.session_state.eval_label = f"{planner} / {split}"
        except Exception as exc:
            notice_error(exc)
    if st.session_state.get("hosted_experiment_url"):
        st.success("Your experiment is ready in LangSmith.")
        st.link_button("View experiment results ↗", st.session_state.hosted_experiment_url)
    if st.session_state.get("eval_rows"):
        rows = st.session_state.eval_rows
        st.markdown(f"**Results · {st.session_state.eval_label}**")
        st.metric("Successful outcomes", f"{sum(r['outcome_pass'] for r in rows)} / {len(rows)}")
        st.dataframe(rows, hide_index=True, width="stretch")
        st.download_button("Download evaluation scores", json.dumps(rows, indent=2), "eval-results.json", "application/json")


def main():
    styling()
    if "rescue" not in st.session_state:
        query_thread = st.query_params.get("rescue")
        if query_thread:
            try:
                set_active(load_rescue(query_thread))
            except ValueError:
                st.query_params.clear()
    sidebar()
    masthead()
    rescue = st.session_state.get("rescue")
    world = rescue["state"]["world"] if rescue else make_world()
    plan = rescue["state"].get("plan", {}) if rescue else {}
    donation = rescue["state"]["donation"] if rescue else None
    reserved = sum(r["quantity"] for r in world["reservations"] if donation and r["donation_id"] == donation["id"])
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Meals in this donation", donation["quantity"] if donation else "—")
    m2.metric("Meals proposed", sum(l["quantity"] for l in plan.get("legs", [])))
    m3.metric("Meals scheduled", reserved)
    m4.metric("Drivers available", sum(d["available"] and not any(r["driver_id"] == d["id"] for r in world["reservations"]) for d in world["drivers"]))
    board, agent, lab = st.tabs(["Dispatch board", "Agent activity", "Evaluation lab"])
    with board:
        left, right = st.columns([1.05, 1.35], gap="large")
        with left:
            with st.container(border=True):
                intake()
            if rescue:
                st.markdown("#### Across the neighborhood")
                neighborhood_map(world, donation, plan)
        with right:
            if rescue:
                rescue_board(rescue)
            else:
                st.markdown("### A city of good connections")
                st.write("Choose a donation on the left. We'll find a way from a kitchen's surplus to a neighbor's next meal.")
                neighborhood_map(world)
                st.html('<div class="stepbar">Find a match. Check the details. You make the call.</div>')
                st.caption("Already made a plan in the CLI? Open it from the sidebar and approve it here.")
    with agent:
        activity(rescue)
    with lab:
        evaluations()
    st.html('<div class="small-note">SECOND SERVING / SAN FRANCISCO &nbsp; · &nbsp; A classroom simulation built around community. Meals shown as scheduled have not been collected or delivered.</div>')


if __name__ == "__main__":
    main()
