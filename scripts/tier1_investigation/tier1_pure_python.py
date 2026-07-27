import re
import json


def parse_top_level_rects(script_text, layer_num):
    """Pure-regex extraction of every top-level cell_Block1 rectangle on the
    given layer. Same technique as M4.S.5's find_m4s5_candidates."""
    poly_def_re = re.compile(r"^(p\d+) = pya\.Polygon\(\[(.*?)\]\)\s*$", re.MULTILINE)
    insert_re = re.compile(
        rf"^cell_Block1\.shapes\(layout\.layer\(pya\.LayerInfo\({layer_num}, 0\)\)\)\.insert\((p\d+)\)\s*$",
        re.MULTILINE)
    point_re = re.compile(r"Point\((-?\d+),\s*(-?\d+)\)")

    poly_info = {}
    for m in poly_def_re.finditer(script_text):
        pts = [(int(a), int(b)) for a, b in point_re.findall(m.group(2))]
        if len(pts) != 4:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if len(set(xs)) != 2 or len(set(ys)) != 2:
            continue
        poly_info[m.group(1)] = {
            "bbox": (min(xs), min(ys), max(xs), max(ys)),
            "points_text_span": (m.start(2), m.end(2)),
        }

    rects = []
    for m in insert_re.finditer(script_text):
        var = m.group(1)
        info = poly_info.get(var)
        if not info:
            continue
        rects.append({"var": var, **info})
    return rects


def rects_overlap(a, b):
    al, ab, ar, at = a
    bl, bb, br, bt = b
    return not (ar <= bl or br <= al or at <= bb or bt <= ab)


def find_spacing_increase_candidates(script_text, layer_num, edges, required_gap_raw, margin_raw=16):
    """edges: [(x1,y1,x2,y2), (x1,y1,x2,y2)] - the two facing edges from the
    DRC report's own edge_pair violation. Finds, for EITHER side, a top-level
    cell_Block1 rectangle whose bbox has an edge exactly matching one of the
    two given edges, and computes the minimal shift (of that edge, away from
    the other) needed to clear required_gap_raw + margin_raw. Independently
    obstacle-checks every candidate against every other top-level rectangle
    on the same layer."""
    rects = parse_top_level_rects(script_text, layer_num)
    candidates = []

    e1, e2 = edges
    horizontal = (e1[1] == e1[3]) and (e2[1] == e2[3])  # both edges horizontal (Y-gap)
    vertical = (e1[0] == e1[2]) and (e2[0] == e2[2])    # both edges vertical (X-gap)

    for edge in (e1, e2):
        ex0, ey0, ex1, ey1 = edge
        if horizontal:
            ey = ey0  # constant Y of this edge
            exlo, exhi = min(ex0, ex1), max(ex0, ex1)
            other_ey = e2[1] if edge is e1 else e1[1]
            for r in rects:
                rl, rb, rr, rt = r["bbox"]
                # this rect's top or bottom edge must equal ey, and its
                # X-range must cover the edge's X-range (with slack)
                if rt == ey and rl <= exhi and rr >= exlo:
                    # this rect sits BELOW the gap (its top edge is ey);
                    # to increase the gap, move its top edge DOWN (away)
                    direction = -1 if other_ey > ey else 1
                    new_rt = ey + direction * (required_gap_raw + margin_raw) \
                        if direction == 1 else ey - (required_gap_raw + margin_raw - 0)
                    # simpler: recompute directly from other_ey
                    if other_ey > ey:
                        new_rt = other_ey - required_gap_raw - margin_raw
                        edge_field = "top"
                    else:
                        continue  # shouldn't happen given rt==ey convention below
                    if new_rt >= rt or new_rt <= rb + 40:  # would grow, or leave <10nm remaining height
                        continue
                    new_bbox = (rl, rb, rr, new_rt)
                    obstacle = any(rects_overlap(new_bbox, q["bbox"])
                                   for q in rects if q["var"] != r["var"])
                    candidates.append({
                        "action": "shrink_top_edge", "var": r["var"],
                        "orig_bbox": r["bbox"], "new_bbox": new_bbox,
                        "shift_nm": (rt - new_rt) / 4.0, "obstacle_free": not obstacle,
                        "edge_field": "max_y",
                    })
                if rb == ey and rl <= exhi and rr >= exlo:
                    if other_ey < ey:
                        new_rb = other_ey + required_gap_raw + margin_raw
                        edge_field = "bottom"
                    else:
                        continue
                    if new_rb <= rb or new_rb >= rt - 40:
                        continue
                    new_bbox = (rl, new_rb, rr, rt)
                    obstacle = any(rects_overlap(new_bbox, q["bbox"])
                                   for q in rects if q["var"] != r["var"])
                    candidates.append({
                        "action": "shrink_bottom_edge", "var": r["var"],
                        "orig_bbox": r["bbox"], "new_bbox": new_bbox,
                        "shift_nm": (new_rb - rb) / 4.0, "obstacle_free": not obstacle,
                        "edge_field": "min_y",
                    })
        elif vertical:
            ex = ex0
            eylo, eyhi = min(ey0, ey1), max(ey0, ey1)
            other_ex = e2[0] if edge is e1 else e1[0]
            for r in rects:
                rl, rb, rr, rt = r["bbox"]
                if rr == ex and rb <= eyhi and rt >= eylo:
                    if other_ex > ex:
                        new_rr = other_ex - required_gap_raw - margin_raw
                    else:
                        continue
                    if new_rr >= rr or new_rr <= rl + 40:
                        continue
                    new_bbox = (rl, rb, new_rr, rt)
                    obstacle = any(rects_overlap(new_bbox, q["bbox"])
                                   for q in rects if q["var"] != r["var"])
                    candidates.append({
                        "action": "shrink_right_edge", "var": r["var"],
                        "orig_bbox": r["bbox"], "new_bbox": new_bbox,
                        "shift_nm": (rr - new_rr) / 4.0, "obstacle_free": not obstacle,
                        "edge_field": "max_x",
                    })
                if rl == ex and rb <= eyhi and rt >= eylo:
                    if other_ex < ex:
                        new_rl = other_ex + required_gap_raw + margin_raw
                    else:
                        continue
                    if new_rl <= rl or new_rl >= rr - 40:
                        continue
                    new_bbox = (new_rl, rb, rr, rt)
                    obstacle = any(rects_overlap(new_bbox, q["bbox"])
                                   for q in rects if q["var"] != r["var"])
                    candidates.append({
                        "action": "shrink_left_edge", "var": r["var"],
                        "orig_bbox": r["bbox"], "new_bbox": new_bbox,
                        "shift_nm": (new_rl - rl) / 4.0, "obstacle_free": not obstacle,
                        "edge_field": "min_x",
                    })

    # de-dup identical candidates (both edges might independently find the same rect)
    seen = set()
    uniq = []
    for c in candidates:
        key = (c["var"], c["action"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


if __name__ == "__main__":
    script_text = open(
        "/home/defyscience/asu_eval/result/t19-m4s5-allblocks/block/repair/Block1/Block1_repaired.py"
    ).read()

    def um(v):
        return round(v * 4000)

    tests = [
        ("M3.S.2[0]", 30, [(um(3.469), um(0.827), um(3.469), um(0.793)),
                            (um(3.492), um(0.793), um(3.492), um(0.827))], 100),
        ("M3.S.2[1]", 30, [(um(0.769), um(3.527), um(0.769), um(3.493)),
                            (um(0.792), um(3.493), um(0.792), um(3.527))], 100),
        ("M2.S.1[0]", 20, [(um(3.374), um(0.845), um(3.474), um(0.845)),
                            (um(3.492), um(0.846), um(3.356), um(0.846))], 72),
        ("M2.S.1[1]", 20, [(um(3.282), um(1.051), um(3.182), um(1.051)),
                            (um(3.1655), um(1.044), um(3.2985), um(1.044))], 72),
        ("M2.S.1[2]", 20, [(um(0.774), um(2.395), um(0.674), um(2.395)),
                            (um(0.656), um(2.394), um(0.792), um(2.394))], 72),
    ]
    for name, layer, edges, req_gap in tests:
        cands = find_spacing_increase_candidates(script_text, layer, edges, req_gap)
        print(f"=== {name} ({len(cands)} candidate(s)) ===")
        for c in cands:
            print(f"   {c['action']} on {c['var']} shift={c['shift_nm']}nm "
                  f"obstacle_free={c['obstacle_free']} new_bbox={c['new_bbox']}")
