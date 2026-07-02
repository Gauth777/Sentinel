from typing import Any, Dict, Optional

def _normalize_prediction(pred: Any) -> Dict[str, Any]:
    if not pred:
        return {}
    if hasattr(pred, "model_dump"):
        pred = pred.model_dump(by_alias=True)
    if not isinstance(pred, dict):
        return {}
    
    # Map both snake_case and camelCase to camelCase
    mapping = {
        "road_type": "roadType",
        "roadtype": "roadType",
        "roadType": "roadType",
        "traffic_density": "trafficDensity",
        "trafficdensity": "trafficDensity",
        "trafficDensity": "trafficDensity",
        "road_complexity": "roadComplexity",
        "roadcomplexity": "roadComplexity",
        "roadComplexity": "roadComplexity",
        "hazard_presence": "hazardPresence",
        "hazardpresence": "hazardPresence",
        "hazardPresence": "hazardPresence",
        "anticipated_risk": "anticipatedRisk",
        "anticipatedrisk": "anticipatedRisk",
        "anticipatedRisk": "anticipatedRisk",
        "recommended_action": "recommendedAction",
        "recommendedaction": "recommendedAction",
        "recommendedAction": "recommendedAction",
    }
    
    res = {}
    for k, v in pred.items():
        mapped_key = mapping.get(k)
        if mapped_key:
            res[mapped_key] = v.value if hasattr(v, "value") else str(v)
    return res

def compute_evidence_data(
    sample_id: str,
    source_sample_id: Optional[str],
    expected_labels: Optional[Dict[str, Any]],
    actual_prediction: Optional[Dict[str, Any]],
    inference_mode: str,
    model: str,
) -> Dict[str, Any]:
    norm_exp = _normalize_prediction(expected_labels)
    norm_act = _normalize_prediction(actual_prediction)
    
    fields = [
        "roadType",
        "trafficDensity",
        "roadComplexity",
        "hazardPresence",
        "anticipatedRisk",
        "recommendedAction",
    ]
    
    field_matches = {}
    correct_count = 0
    
    for f in fields:
        exp_val = norm_exp.get(f)
        act_val = norm_act.get(f)
        match = (exp_val is not None) and (act_val is not None) and (str(exp_val) == str(act_val))
        field_matches[f] = match
        if match:
            correct_count += 1
            
    return {
        "localSampleId": sample_id,
        "sourceSampleId": source_sample_id,
        "expectedLabels": norm_exp if expected_labels else None,
        "actualPrediction": norm_act if actual_prediction else None,
        "fieldMatches": field_matches,
        "correctFieldCount": correct_count,
        "totalFieldCount": 6,
        "inferenceMode": inference_mode,
        "model": model,
        "sampleId": sample_id,
        "sourceMapAvailable": source_sample_id is not None,
    }
