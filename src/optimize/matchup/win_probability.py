"""Normal-distribution win probability with opponent pickup boost."""

from scipy.stats import norm

from src.optimize.models import MatchupContext, WinProbability


def compute_win_probability(ctx: MatchupContext) -> WinProbability:
    my_proj = ctx.my_projection
    opp_proj = ctx.opp_projection
    my_boost = ctx.my_pickup_boost
    opp_boost = ctx.opp_pickup_boost

    my_total = my_proj.earned + my_proj.mu_remaining + my_boost.mu_boost
    opp_total = opp_proj.earned + opp_proj.mu_remaining + opp_boost.mu_boost

    gap = my_total - opp_total
    combined_var = (
        my_proj.sigma_remaining**2
        + opp_proj.sigma_remaining**2
        + my_boost.sigma_boost**2
        + opp_boost.sigma_boost**2
    )
    combined_sigma = combined_var**0.5

    if combined_sigma == 0:
        p_win = 1.0 if gap > 0 else (0.5 if gap == 0 else 0.0)
    else:
        p_win = float(norm.cdf(gap / combined_sigma))

    def _breakdown(label: str, total: float, proj, boost) -> str:
        return (
            f"{label} projected: {total:.1f} (earned {proj.earned:.1f} "
            f"+ remaining {proj.mu_remaining:.1f} "
            f"+ pickup boost {boost.mu_boost:.1f})"
        )

    reasoning = [
        _breakdown("My", my_total, my_proj, my_boost),
        _breakdown("Opp", opp_total, opp_proj, opp_boost),
        f"Gap: {gap:+.1f}, sigma: {combined_sigma:.1f}",
        f"P(win): {p_win:.3f}",
    ]

    return WinProbability(
        p_win=p_win,
        projected_gap=gap,
        combined_sigma=combined_sigma,
        my_total=my_total,
        opp_total=opp_total,
        reasoning=reasoning,
    )
