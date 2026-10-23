"""Statistical analysis of airline reliability.

Outcome: unreliable = arrived 15+ min late (BTS definition), cancelled, or diverted. Linear probability
models, so coefficients are percentage-point differences.

Q1  Raw and like-for-like (same routes) rates per carrier.
Q2  What drives reliability? Contribution of each factor block (drop-one R^2), plus the size of
    hour-of-day, month, weather and aircraft-age effects.
Q3  Carrier effects after controls: route FE + origin-airport x date FE (absorbs that day's weather,
    ATC programs, airport congestion) + scheduled-hour FE. Two-way clustered SEs (date, route).
    Effects are expressed relative to the flight-weighted average carrier.
Also: empirical-Bayes shrunk route x carrier rates with intervals for the passenger app.

Usage:  python src/analysis.py
"""
import json

import duckdb
import numpy as np
import pandas as pd
import pyfixest as pf
from statsmodels.stats.proportion import proportion_confint

from config import DB_PATH, MIN_CARRIER_FLIGHTS, OUT_DIR

VCOV = {"CRV1": "flight_date+route"}


def load(con):
    df = con.execute("SELECT * FROM flights").df()
    df["flight_date"] = df["flight_date"].astype(str)
    df["unreliable"] = df["unreliable"].astype(float) * 100        # percentage points
    df["cancelled_pp"] = df["cancelled"].astype(float) * 100
    df["origin_date"] = df["origin"] + "_" + df["flight_date"]
    df["plane_age"] = 2013 - df["plane_year"]
    df["age_band"] = pd.cut(df["plane_age"], [-1, 5, 10, 15, 20, 60],
                            labels=["0-5", "6-10", "11-15", "16-20", "21+"]).astype(str)
    df["rain"] = (df["precip"].fillna(0) > 0).astype(int)
    df["low_vis"] = (df["visib"].fillna(10) < 3).astype(int)
    df["strong_wind"] = (df["wind_speed"].fillna(0) >= 20).astype(int)
    return df


def carrier_effects(df, outcome, carriers, label):
    """Carrier fixed effects re-expressed as deviations from the flight-weighted average carrier."""
    sub = df[df.carrier.isin(carriers)].copy()
    ref = carriers[0]
    fit = pf.feols(f"{outcome} ~ i(carrier, ref='{ref}') | route + origin_date + sched_hour", data=sub, vcov=VCOV)
    names = list(fit._coefnames)
    b = pd.Series(np.asarray(fit.coef()), index=names)
    V = pd.DataFrame(np.asarray(fit._vcov), index=names, columns=names)
    full = {c: (0.0 if c == ref else b[[n for n in names if n.endswith(f"[T.{c}]") or n.endswith(f"::{c}")][0]])
            for c in carriers}
    key = {c: (None if c == ref else [n for n in names if n.endswith(f"[T.{c}]") or n.endswith(f"::{c}")][0])
           for c in carriers}
    w = sub.carrier.value_counts(normalize=True).reindex(carriers).values
    rows = []
    for j, c in enumerate(carriers):
        vec = pd.Series(0.0, index=names)
        for i, c2 in enumerate(carriers):         # contrast: e_c - sum_i w_i e_i
            if key[c2] is not None:
                vec[key[c2]] -= w[i]
        if key[c] is not None:
            vec[key[c]] += 1
        est = float(sum(full[c2] * ((1 if c2 == c else 0) - w[i]) for i, c2 in enumerate(carriers)))
        se = float(np.sqrt(vec.values @ V.values @ vec.values))
        rows.append({"carrier": c, "model": label, "effect_pp": est, "se": se,
                     "ci_low": est - 1.96 * se, "ci_high": est + 1.96 * se,
                     "significant": bool(abs(est) > 1.96 * se), "flights": int((sub.carrier == c).sum())})
    return pd.DataFrame(rows).sort_values("effect_pp", ascending=False), int(fit._N)


def drop_one_r2(df):
    """How much explained variance each block adds when added last (LPM, R^2 from residuals)."""
    blocks = {"carrier": "carrier", "route": "route", "origin x date (daily conditions)": "origin_date",
              "scheduled hour": "sched_hour", "month": "month", "weekday": "dow"}
    d = df.dropna(subset=["unreliable"]).copy()
    # pyfixest needs one regressor; independent noise leaves the fixed-effect residuals (and R^2) unchanged
    d["_noise"] = np.random.default_rng(0).standard_normal(len(d))
    tss = float(((d.unreliable - d.unreliable.mean()) ** 2).sum())

    def r2(fes):
        m = pf.feols("unreliable ~ _noise | " + " + ".join(fes), data=d)
        return 1 - float((np.asarray(m.resid()) ** 2).sum()) / tss

    r2_full = r2(list(blocks.values()))
    out = {"r2_full": r2_full, "drop_one": {}, "alone": {}}
    for name, fe in blocks.items():
        out["drop_one"][name] = r2_full - r2([v for v in blocks.values() if v != fe])
        out["alone"][name] = r2([fe])
    return out


def factor_effects(df):
    """Hour, weather and aircraft-age effects controlling for carrier, route and month (not daily FE,
    which would absorb weather)."""
    d = df.copy()
    res = {}
    m = pf.feols("unreliable ~ rain + low_vis + strong_wind | carrier + route + month + sched_hour",
                 data=d, vcov={"CRV1": "flight_date"})
    t = m.tidy()
    res["weather_pp"] = {k: {"effect_pp": float(t.loc[k, "Estimate"]),
                             "ci": [float(t.loc[k, "2.5%"]), float(t.loc[k, "97.5%"])]}
                         for k in ("rain", "low_vis", "strong_wind")}
    a = d[d.age_band != "nan"]
    m2 = pf.feols("unreliable ~ i(age_band, ref='0-5') | carrier + route + origin_date + sched_hour",
                  data=a, vcov=VCOV)
    t2 = m2.tidy()
    res["aircraft_age_vs_0_5yrs_pp"] = {i.split("::")[-1].replace("]", "").replace("T.", ""): {
        "effect_pp": float(r["Estimate"]), "ci": [float(r["2.5%"]), float(r["97.5%"])]}
        for i, r in t2.iterrows()}
    hour = d.groupby("sched_hour").agg(rate=("unreliable", "mean"), n=("unreliable", "size"))
    hour = hour[hour.n >= 1000]
    month = d.groupby("month")["unreliable"].mean()
    res["hour_range"] = {"best_hour": int(hour.rate.idxmin()), "best_rate_pct": float(hour.rate.min()),
                         "worst_hour": int(hour.rate.idxmax()), "worst_rate_pct": float(hour.rate.max())}
    res["month_range"] = {"best_month": int(month.idxmin()), "best_rate_pct": float(month.min()),
                          "worst_month": int(month.idxmax()), "worst_rate_pct": float(month.max())}
    return res


def route_carrier_eb(con):
    """Empirical-Bayes (beta-binomial) shrinkage of route x carrier unreliability for small samples."""
    rc = con.execute("SELECT * FROM route_carrier WHERE flights >= 20").df()
    out = []
    for route, g in rc.groupby("route"):
        p = g.unreliable_flights.sum() / g.flights.sum()
        var = np.average((g.unreliable_rate - p) ** 2, weights=g.flights)
        binom = p * (1 - p) / g.flights.mean()
        tau2 = max(var - binom, 1e-5)
        k = max(p * (1 - p) / tau2 - 1, 1.0)                      # prior strength (pseudo-flights)
        a0, b0 = p * k, (1 - p) * k
        for r in g.itertuples():
            lo, hi = proportion_confint(r.unreliable_flights, r.flights, method="wilson")
            out.append({"route": route, "carrier": r.carrier, "flights": r.flights, "raw_rate": r.unreliable_rate,
                        "wilson_low": lo, "wilson_high": hi,
                        "eb_rate": (r.unreliable_flights + a0) / (r.flights + a0 + b0), "prior_flights": k})
    eb = pd.DataFrame(out)
    con.execute("CREATE OR REPLACE TABLE route_carrier_eb AS SELECT * FROM eb")
    return eb


def main():
    con = duckdb.connect(str(DB_PATH))
    df = load(con)
    counts = df.carrier.value_counts()
    carriers = [c for c in counts.index if counts[c] >= MIN_CARRIER_FLIGHTS]

    print("Q3: adjusted carrier effects ...")
    eff_all, n_all = carrier_effects(df, "unreliable", carriers, "all routes")
    comp = con.execute("SELECT route FROM competitive_routes").df().route
    dc = df[df.route.isin(comp)]
    cc = [c for c in carriers if (dc.carrier == c).sum() >= MIN_CARRIER_FLIGHTS]
    eff_comp, n_comp = carrier_effects(dc, "unreliable", cc, "competitive routes only")
    eff_cancel, _ = carrier_effects(df, "cancelled_pp", carriers, "cancellation")

    print("Q2: factor contributions ...")
    r2 = drop_one_r2(df)
    fx = factor_effects(df)

    print("Route x carrier shrinkage ...")
    eb = route_carrier_eb(con)

    raw = con.execute("SELECT * FROM carrier_summary").df()
    like = con.execute("SELECT * FROM carrier_route_compare").df()
    effects = pd.concat([eff_all, eff_comp, eff_cancel])
    con.execute("CREATE OR REPLACE TABLE carrier_effects AS SELECT * FROM effects")
    sig_worse = eff_all[(eff_all.significant) & (eff_all.effect_pp > 0)]
    sig_better = eff_all[(eff_all.significant) & (eff_all.effect_pp < 0)]
    raw_k = raw[raw.carrier.isin(carriers)].set_index("carrier")
    res = {
        "data": json.loads((OUT_DIR / "build_stats.json").read_text()),
        "carriers_ranked": carriers,
        "q1_raw": raw.to_dict(orient="records"),
        "q1_like_for_like": like.to_dict(orient="records"),
        "q2_drop_one_r2": r2,
        "q2_effects": fx,
        "q3_carrier_effects": effects.to_dict(orient="records"),
        "q3_summary": {
            "n_flights_model": n_all, "n_flights_competitive_model": n_comp,
            "significantly_worse": sig_worse.carrier.tolist(), "significantly_better": sig_better.carrier.tolist(),
            "worst": eff_all.iloc[0].to_dict(), "best": eff_all.iloc[-1].to_dict(),
            "spread_pp": float(eff_all.effect_pp.max() - eff_all.effect_pp.min()),
            "raw_spread_pp": float(100 * (raw_k.unreliable_rate.max() - raw_k.unreliable_rate.min())),
            "rank_corr_all_vs_competitive": float(eff_all.set_index("carrier").effect_pp.reindex(cc)
                                                  .corr(eff_comp.set_index("carrier").effect_pp.reindex(cc),
                                                        method="spearman")),
        },
        "route_carrier_eb": {"pairs": int(len(eb)), "median_prior_flights": float(eb.prior_flights.median())},
    }
    (OUT_DIR / "results.json").write_text(json.dumps(res, indent=2, default=float))
    print(eff_all.round(2).to_string(index=False))
    print(json.dumps(r2, indent=1))
    print(json.dumps(fx, indent=1))
    print(json.dumps(res["q3_summary"], indent=1, default=float))


if __name__ == "__main__":
    main()
