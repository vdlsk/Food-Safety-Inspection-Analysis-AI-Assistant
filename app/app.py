"""FoodSafe Intelligence. Run:  streamlit run app/app.py"""
import json
import sys
from pathlib import Path

import duckdb
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from config import DB_PATH, OUT_DIR  # noqa: E402

st.set_page_config(page_title="FoodSafe Intelligence", layout="wide")


@st.cache_resource
def con():
    return duckdb.connect(str(DB_PATH), read_only=True)


@st.cache_resource
def assistant():
    from assistant import FoodSafe
    return FoodSafe()


def q(sql, params=None):
    return con().execute(sql, params or []).df()


S = json.loads((OUT_DIR / "stats_results.json").read_text())
st.title("FoodSafe Intelligence")
st.caption(f"Chicago food inspections {S['data']['date_range'][0]} to {S['data']['date_range'][1]} · "
           f"{S['data']['inspections']:,} inspections · {S['data']['establishments']:,} establishments")
tabs = st.tabs(["Overview", "Establishment lookup", "Repeat-failure statistics", "Ask FoodSafe",
                "Observation -> provision", "Limitations"])

with tabs[0]:
    yr = q("""SELECT year(inspection_date) AS year, inspection_class, avg(failed) AS fail_rate, count(*) AS n
              FROM inspections WHERE has_outcome GROUP BY ALL ORDER BY year""")
    st.plotly_chart(px.line(yr, x="year", y="fail_rate", color="inspection_class", markers=True,
                            title="Fail rate by year and inspection type"), use_container_width=True)
    rec = q("SELECT * FROM violation_recurrence ORDER BY establishments_with_repeat DESC LIMIT 15")
    st.plotly_chart(px.bar(rec, x="establishments_with_repeat", y="violation_title", orientation="h",
                           hover_data=["violation_no", "p_recur", "p_new", "recurrence_lift"],
                           title="Violations most often repeated by the same establishment (since July 2018)"),
                    use_container_width=True)

with tabs[1]:
    name = st.text_input("Search by name, address or license #", "")
    if name:
        res = assistant().find(name)
        if res.empty:
            st.warning("No match.")
        else:
            pick = st.selectbox("Establishment", res.license_no,
                                format_func=lambda x: f"{res.set_index('license_no').loc[x, 'name']} · "
                                                      f"{res.set_index('license_no').loc[x, 'address']} · #{x}")
            fs = assistant()
            peer = fs.peer_stats(int(pick))
            c = st.columns(3)
            e = res.set_index("license_no").loc[pick]
            c[0].metric("Inspections", int(e.n_inspections))
            c[1].metric("Failures", int(e.n_fails))
            if peer:
                c[2].metric("Failures vs peer expectation", f"{peer['ratio']:.2f}x",
                            "statistically unusual" if peer["flagged"] else "within expected range",
                            delta_color="inverse" if peer["flagged"] else "off")
            st.subheader("History"); st.dataframe(fs.history(int(pick), 25), use_container_width=True)
            st.subheader("Repeated violations"); st.dataframe(fs.repeat(int(pick)), use_container_width=True)

with tabs[2]:
    c2 = S["q2_state_dependence"]["canvass_to_canvass"]
    cr, a1, a2 = c2["crude"], c2["adjusted"], c2["adjusted_for_establishment_history"]
    st.markdown(f"""
**Does failing one routine inspection go with failing the next?** ({cr['n_pairs']:,} consecutive canvass pairs,
{c2['n_establishments']:,} establishments; re-inspections excluded because failures trigger them)

| | Odds ratio | 95% CI | p |
|---|---|---|---|
| Crude | {cr['odds_ratio']:.2f} | {cr['or_ci95'][0]:.2f}-{cr['or_ci95'][1]:.2f} | {cr['p_value']:.2g} |
| + facility type, risk, year, gap, prior count | {a1['odds_ratio']:.2f} | {a1['or_ci95'][0]:.2f}-{a1['or_ci95'][1]:.2f} | {a1['p_value']:.2g} |
| + establishment's record on other inspections | {a2['odds_ratio']:.2f} | {a2['or_ci95'][0]:.2f}-{a2['or_ci95'][1]:.2f} | {a2['p_value']:.2g} |

Fail rate after a failure: **{100 * cr['p_fail_after_fail']:.1f}%**, after a pass: **{100 * cr['p_fail_after_pass']:.1f}%**.
Average marginal effect (adjusted): {a1['avg_marginal_effect_pp']:+.1f} percentage points.
""")
    dose = c2["dose_response"]
    fig = go.Figure(go.Bar(x=[f"{d['prior_fails_of_last_2']} of last 2 failed" for d in dose],
                           y=[d["fail_rate"] for d in dose],
                           error_y=dict(type="data", symmetric=False,
                                        array=[d["ci95"][1] - d["fail_rate"] for d in dose],
                                        arrayminus=[d["fail_rate"] - d["ci95"][0] for d in dose])))
    fig.update_layout(title="Next routine-inspection fail rate by recent history (95% Wilson CI)")
    st.plotly_chart(fig, use_container_width=True)
    fl = q("SELECT * FROM establishment_flags")
    st.plotly_chart(px.scatter(fl, x="expected", y="observed", color="flagged", hover_data=["name", "n", "q_value"],
                               title="Observed vs peer-expected failures (flagged: FDR q < 0.05)", opacity=0.5),
                    use_container_width=True)

with tabs[3]:
    term = st.text_input("Establishment (name or license #)", key="ask_term")
    question = st.text_input("Question", "Why does this place keep failing, and which regulations are relevant?")
    if term:
        res = assistant().find(term)
        if not res.empty:
            lic = st.selectbox("Pick", res.license_no, key="ask_pick",
                               format_func=lambda x: f"{res.set_index('license_no').loc[x, 'name']} #{x}")
            if st.button("Ask", type="primary"):
                out = assistant().ask(int(lic), question)
                st.markdown(out["answer"])
                g = out["grounding"]
                st.caption(f"Grounding check: {g['numbers_supported']}/{g['numbers']} figures found in context · "
                           f"{g['reg_citations_valid']}/{g['reg_citations']} regulation citations valid · "
                           f"{g['record_citations_valid']}/{g['record_citations']} record citations valid")
                with st.expander("Retrieved provisions"):
                    for i, r in out["parts"]["regs"].iterrows():
                        st.markdown(f"**[R{i + 1}] {r.section}** · {r.doc}\n\n{r.text[:800]}")
                with st.expander("Full context sent to the model"):
                    st.text(out["context"])

with tabs[4]:
    st.write("Type what you observed. Similar past observations vote on the violation number; "
             "the matching code requirements are shown for the inspector to confirm.")
    obs = st.text_area("Observation", "OBSERVED MOUSE DROPPINGS UNDER THE PREP TABLE AND ALONG THE REAR WALL")
    if st.button("Suggest violation and provisions"):
        from cases import CaseRetriever
        import re as _re
        sugg, nb = st.cache_resource(CaseRetriever)().suggest(obs)
        chunks = assistant().chunks
        for sgg in sugg:
            st.markdown(f"**#{sgg['violation_no']} {sgg['violation_title']}** · vote share {100 * sgg['vote_share']:.0f}%")
            sec = chunks[chunks.section.str.match(rf"Section {sgg['violation_no']}\b")]
            for t in sec.text.head(1):
                st.caption(t[:700])
        with st.expander("Most similar past observations"):
            st.dataframe(nb, use_container_width=True)
    ev = OUT_DIR / "observation_retrieval_results.json"
    if ev.exists():
        e = json.loads(ev.read_text())
        st.caption(f"Held-out evaluation ({e['n_queries']:,} observations from {e['test_from'][:4]} onward): "
                   f"top-1 accuracy {e['case_based']['hit@1']:.2f}, top-3 {e['case_based']['hit@3']:.2f} "
                   f"(most-frequent baseline {e['baseline_most_frequent']['hit@1']:.2f} / {e['baseline_most_frequent']['hit@3']:.2f}).")

with tabs[5]:
    st.markdown("""
- A license number is used as the establishment ID; ownership changes and multiple licenses at one site are not merged.
- Inspection frequency depends on risk level and complaints, so establishments are not sampled equally.
- Results reflect inspector findings on the day, and inspectors differ. Violation numbering changed on 1 July 2018;
  violation-level analysis uses the current code only.
- Associations are not causes: a failure followed by another failure may reflect persistent conditions rather than
  any effect of the first failure. The third model attempts to separate the two but cannot rule out other confounders.
- Statistical flags identify establishments worth review, not legal non-compliance.
- The assistant's regulatory answers depend on which documents were ingested and must be confirmed by an inspector.
""")
