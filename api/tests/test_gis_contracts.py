from pydantic import ValidationError

from app.gis.schemas import FacilityCategory, IsochroneQuery, Origin, SiteAnalysisResponse


VALID_CATEGORIES: tuple[FacilityCategory, ...] = (
    "education",
    "healthcare",
    "daily_shopping",
    "parks",
    "public_transport",
)


def build_site_analysis_response(
    *,
    scores: dict[str, float | None],
    score_status: str = "ok",
) -> SiteAnalysisResponse:
    return SiteAnalysisResponse(
        origin=Origin(lng=-122.4194, lat=37.7749),
        catchment={"type": "FeatureCollection", "features": []},
        population=1000,
        population_method="按土地面积比例估算",
        metrics={category: {"value": 1, "unit": "每千人"} for category in VALID_CATEGORIES},
        facilities={"type": "FeatureCollection", "features": []},
        scores=scores,
        score_status=score_status,
        data_version="test",
        acs_year=2023,
        scoring_method="湾区人口加权相对分数",
    )


def test_isochrone_query_rejects_unapproved_duration() -> None:
    try:
        IsochroneQuery(
            origin=Origin(lng=-122.4194, lat=37.7749),
            mode="walking",
            minutes=20,
        )
    except ValidationError:
        return
    raise AssertionError("20 分钟必须被拒绝")


def test_origin_rejects_point_outside_bay_area() -> None:
    try:
        Origin(lng=-118.2437, lat=34.0522)
    except ValidationError:
        return
    raise AssertionError("湾区外坐标必须被拒绝")


def test_gis_error_rejects_unknown_code_and_uses_fixed_message() -> None:
    from app.gis.errors import GisError

    try:
        GisError("UNKNOWN", "内部连接信息", 500)
    except ValueError:
        pass
    else:
        raise AssertionError("未知错误码必须被拒绝")

    error = GisError("VALHALLA_UNAVAILABLE", "上游原始异常文本", 503)
    assert error.message == "路网分析服务暂不可用"
    assert error.to_response() == {
        "code": "VALHALLA_UNAVAILABLE",
        "message": "路网分析服务暂不可用",
    }


def test_site_analysis_rejects_score_above_100() -> None:
    scores = {category: 0.0 for category in VALID_CATEGORIES}
    scores["education"] = 101.0
    try:
        build_site_analysis_response(scores=scores)
    except ValidationError:
        return
    raise AssertionError("分项分数不能超过 100")


def test_site_analysis_rejects_non_finite_score() -> None:
    scores = {category: 0.0 for category in VALID_CATEGORIES}
    scores["education"] = float("nan")
    try:
        build_site_analysis_response(scores=scores)
    except ValidationError:
        return
    raise AssertionError("分项分数必须是有限数值")


def test_site_analysis_requires_all_five_score_categories() -> None:
    scores = {category: 0.0 for category in VALID_CATEGORIES[:-1]}
    try:
        build_site_analysis_response(scores=scores)
    except ValidationError:
        return
    raise AssertionError("分项分数必须包含五类设施")


def test_site_analysis_score_status_matches_score_values() -> None:
    scores = {category: None for category in VALID_CATEGORIES}
    try:
        build_site_analysis_response(scores=scores, score_status="ok")
    except ValidationError:
        pass
    else:
        raise AssertionError("ok 状态下五类分数必须都是数值")

    scores["education"] = 50.0
    try:
        build_site_analysis_response(scores=scores, score_status="insufficient_population")
    except ValidationError:
        return
    raise AssertionError("样本不足状态下五类分数必须都是 None")
