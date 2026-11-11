import os
import pytest
import ezdxf
import xml.etree.ElementTree as ET
import create_touchpad_pcb as ctp


def test_is_close_point():
    assert ctp.is_close_point((1.0, 2.0), (1.00001, 2.00001), tol=1e-4)
    assert not ctp.is_close_point((1.0, 2.0), (1.01, 2.0), tol=1e-4)


def test_deduplicate_vertices():
    # Empty
    assert ctp.deduplicate_vertices([]) == []
    # Single vertex
    assert ctp.deduplicate_vertices([(0, 0)]) == [(0, 0)]
    # Consecutive duplicates and closed loop end duplicate
    pts = [(0, 0), (0, 0), (1, 0), (1, 1), (0, 0)]
    assert ctp.deduplicate_vertices(pts) == [(0, 0), (1, 0), (1, 1)]


def test_calculate_center():
    assert ctp.calculate_center([]) == (0.0, 0.0)
    poly = [(0, 0), (4, 0), (4, 4), (0, 4)]
    assert ctp.calculate_center(poly) == (2.0, 2.0)


def test_col_poly_is_up_and_down():
    # Triangle pointing up: bottom vertex (1, 0), top two (0, 2), (2, 2)
    # y_coords sorted: [0, 2, 2] -> y_coords[1] (2) is NOT < y_coords[2] (2)
    # Let's check definition in code:
    # col_poly_is_up: y_coords[1] < y_coords[2] (one top vertex higher than the middle and bottom)
    up_tri = [(0, 0), (2, 0), (1, 3)]
    # y_coords = [0, 0, 3] -> sort: [0, 0, 3], y[1] is 0 < 3 -> True!
    assert ctp.col_poly_is_up(up_tri)
    assert not ctp.col_poly_is_down(up_tri)

    # Triangle pointing down: one vertex at bottom (1, -1), top two at (0, 2), (2, 2)
    # y_coords = [2, 2, -1] -> sort: [-1, 2, 2]
    # col_poly_is_down: y_coords[0] < y_coords[1] (-1 < 2 -> True)
    down_tri = [(0, 2), (2, 2), (1, -1)]
    assert ctp.col_poly_is_down(down_tri)
    assert not ctp.col_poly_is_up(down_tri)

    # Non-triangle
    assert not ctp.col_poly_is_up([(0, 0), (1, 0), (1, 1), (0, 1)])
    assert not ctp.col_poly_is_down([(0, 0), (1, 0), (1, 1), (0, 1)])


def test_row_poly_is_left_and_right():
    # Triangle pointing left: (0, 1), (2, 0), (2, 2)
    # x_coords: [0, 2, 2] -> x[0] < x[1] is 0 < 2 -> True
    left_tri = [(0, 1), (2, 0), (2, 2)]
    assert ctp.row_poly_is_left(left_tri)

    # Triangle pointing right: (0, 0), (0, 2), (3, 1)
    # x_coords: [0, 0, 3] -> x[1] < x[2] is 0 < 3 -> True
    right_tri = [(0, 0), (0, 2), (3, 1)]
    assert ctp.row_poly_is_right(right_tri)


def test_group_polygons_by_center():
    # Polygons centered at x=1.0001, x=1.0004, and x=5.0
    poly1 = [(0, 0), (2, 0), (2, 2), (0, 2)]  # center (1, 1)
    poly2 = [(0.001, 0), (2.001, 0), (2.001, 2), (0.001, 2)]  # center (1.001, 1)
    poly3 = [(4, 0), (6, 0), (6, 2), (4, 2)]  # center (5, 1)

    grouped = ctp.group_polygons_by_center([poly1, poly2, poly3], axis='x', tolerance=0.05)
    # Should cluster poly1 and poly2 together into 1 group, poly3 into another
    assert len(grouped) == 2
    keys = sorted(grouped.keys())
    assert pytest.approx(keys[0], 0.01) == 1.0
    assert pytest.approx(keys[1], 0.01) == 5.0
    assert len(grouped[keys[0]]) == 2
    assert len(grouped[keys[1]]) == 1


def test_create_column_and_row():
    # Column with 3-vertex up triangle, 4-vertex diamond, and 3-vertex down triangle
    poly_up = [(0, 0), (2, 0), (1, 3)]
    poly_quad = [(0, 5), (1, 6), (2, 5), (1, 4)]
    poly_down = [(0, 9), (2, 9), (1, 7)]

    sig_col, pad_col = ctp.create_column(1, [poly_up, poly_quad, poly_down])
    assert sig_col.attrib['name'] == 'COL1'
    assert pad_col.attrib['name'] == 'COL1'

    # Check isolate="0" on column polygons
    polygons = sig_col.findall('polygon')
    assert len(polygons) == 3
    for p in polygons:
        assert p.attrib['isolate'] == '0'

    # Check vias and wires exist in column
    vias = sig_col.findall('via')
    assert len(vias) >= 2
    wires = sig_col.findall('wire')
    assert len(wires) >= 1

    # Check row creation and KiCad isolate="0" compliance
    poly_r1 = [(0, 0), (1, 1), (2, 0), (1, -1)]
    poly_r2 = [(4, 0), (5, 1), (6, 0), (5, -1)]
    sig_row, pad_row = ctp.create_row(1, [poly_r1, poly_r2])
    assert sig_row.attrib['name'] == 'ROW1'
    assert pad_row.attrib['name'] == 'ROW1'
    row_polygons = sig_row.findall('polygon')
    assert len(row_polygons) == 2
    for p in row_polygons:
        assert p.attrib['isolate'] == '0'
    row_wires = sig_row.findall('wire')
    assert len(row_wires) == 1


def test_parameter_validation():
    with pytest.raises(ValueError, match="via_size must be positive"):
        ctp.generate_pcb('dummy.dxf', 'empty.brd', 'empty.sch', 'out.brd', 'out.sch', params={'via_size': -1})

    with pytest.raises(ValueError, match="cannot be larger than via_size"):
        ctp.generate_pcb('dummy.dxf', 'empty.brd', 'empty.sch', 'out.brd', 'out.sch',
                         params={'via_size': 0.3, 'via_drill': 0.5})

    with pytest.raises(ValueError, match="trace_width must be positive"):
        ctp.generate_pcb('dummy.dxf', 'empty.brd', 'empty.sch', 'out.brd', 'out.sch', params={'trace_width': 0})


def test_dxf_parsing_lines_and_polylines(tmp_path):
    dxf_file = str(tmp_path / "test_shapes.dxf")
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()

    # 1. Add polygon made of LINE entities (diamond 1)
    pts1 = [(10, 0), (15, 5), (10, 10), (5, 5)]
    for i in range(len(pts1)):
        p_start = pts1[i]
        p_end = pts1[(i + 1) % len(pts1)]
        msp.add_line(p_start, p_end)

    # 2. Add closed LWPOLYLINE entity (diamond 2)
    pts2 = [(30, 0), (35, 5), (30, 10), (25, 5)]
    msp.add_lwpolyline(pts2, close=True)

    # 3. Add closed 2D POLYLINE entity (diamond 3)
    pts3 = [(50, 0), (55, 5), (50, 10), (45, 5)]
    msp.add_polyline2d(pts3, close=True)

    doc.saveas(dxf_file)

    polygons = ctp.parse_dxf_polygons(dxf_file)
    assert len(polygons) == 3
    # Verify each polygon has 4 vertices
    for p in polygons:
        assert len(p) == 4


def test_end_to_end_generation(tmp_path):
    dxf_file = str(tmp_path / "matrix.dxf")
    out_brd = str(tmp_path / "test_out.brd")
    out_sch = str(tmp_path / "test_out.sch")

    # Generate a synthetic 3x3 diamond matrix DXF
    # In Touchpad generator, columns and rows alternate (odd columns/rows are real electrodes)
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()

    # Create 3 columns (x = 10, 20, 30) and 3 rows (y = 10, 20, 30)
    for x in [10.0, 20.0, 30.0]:
        for y in [10.0, 20.0, 30.0]:
            diamond = [(x, y - 2), (x + 2, y), (x, y + 2), (x - 2, y)]
            msp.add_lwpolyline(diamond, close=True)

    doc.saveas(dxf_file)

    ctp.generate_pcb(
        dxf_input=dxf_file,
        board_template='empty.brd',
        schematic_template='empty.sch',
        output_board=out_brd,
        output_schematic=out_sch,
        params={
            'via_size': 0.4,
            'via_drill': 0.3,
            'via_offset': 0.1,
            'trace_width': 0.1,
            'polygon_line_width': 0.005
        }
    )

    assert os.path.exists(out_brd)
    assert os.path.exists(out_sch)

    # Parse board XML
    brd_tree = ET.parse(out_brd)
    brd_root = brd_tree.getroot()
    signals = brd_root.findall('.//signals/signal')
    elements = brd_root.findall('.//elements/element')

    # Both row and column signals should be present
    sig_names = [s.attrib['name'] for s in signals]
    assert 'COL1' in sig_names
    assert 'ROW1' in sig_names

    # Check isolate="0" everywhere on generated polygons
    for sig in signals:
        for poly in sig.findall('polygon'):
            assert poly.attrib.get('isolate') == '0'

    # Parse schematic XML
    sch_tree = ET.parse(out_sch)
    sch_root = sch_tree.getroot()
    parts = sch_root.findall('.//parts/part')
    part_names = [p.attrib['name'] for p in parts]
    assert 'COL1' in part_names
    assert 'ROW1' in part_names
