import math
import argparse
import xml.etree.ElementTree as ET
from collections import defaultdict
from itertools import tee, islice, chain
import ezdxf

# Default units in mm
DEFAULT_VIA_SIZE = 0.4
DEFAULT_VIA_DRILL = 0.3
DEFAULT_VIA_OFFSET = 0.1
DEFAULT_TRACE_WIDTH = 0.1
DEFAULT_POLYGON_LINE_WIDTH = 0.005

# Backward compatibility module variables
viaSize = DEFAULT_VIA_SIZE
viaDrill = DEFAULT_VIA_DRILL
viaOffset = DEFAULT_VIA_OFFSET
traceWidth = DEFAULT_TRACE_WIDTH
polygonLineWidth = DEFAULT_POLYGON_LINE_WIDTH
dxf_path = 'touchpad.dxf'
boardFile = 'empty.brd'
schematicFile = 'empty.sch'
outputBoardFile = 'Touchpad.brd'
outputSchematicFile = 'Touchpad.sch'


def is_close_point(p1, p2, tol=1e-4):
    """Check if two 2D points are within a distance tolerance."""
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1]) <= tol


def deduplicate_vertices(vertices, tol=1e-4):
    """Remove consecutive duplicate vertices and trailing vertex if closed."""
    if not vertices:
        return []
    cleaned = [vertices[0]]
    for pt in vertices[1:]:
        if not is_close_point(pt, cleaned[-1], tol=tol):
            cleaned.append(pt)
    if len(cleaned) > 1 and is_close_point(cleaned[0], cleaned[-1], tol=tol):
        cleaned.pop()
    return cleaned


def extract_segments_from_dxf(file_path):
    """
    Extract line segments and native polygons from a DXF file.
    Supports LINE, LWPOLYLINE, and POLYLINE entities.
    Returns (raw_lines, closed_polygons).
    """
    try:
        doc = ezdxf.readfile(file_path)
    except IOError as e:
        raise FileNotFoundError(f"Could not read DXF file '{file_path}'") from e
    except ezdxf.DXFStructureError as e:
        raise ValueError(f"Invalid DXF file structure in '{file_path}'") from e

    msp = doc.modelspace()
    lines = []
    closed_polygons = []

    # 1. Standard LINE entities
    for entity in msp.query('LINE'):
        start = (float(entity.dxf.start.x), float(entity.dxf.start.y))
        end = (float(entity.dxf.end.x), float(entity.dxf.end.y))
        lines.append((start, end))

    # 2. LWPOLYLINE entities
    for entity in msp.query('LWPOLYLINE'):
        pts = [(float(p[0]), float(p[1])) for p in entity.get_points(format='xy')]
        if len(pts) < 2:
            continue
        if getattr(entity, 'closed', False):
            dedup = deduplicate_vertices(pts)
            if len(dedup) >= 3:
                closed_polygons.append(dedup)
        else:
            for i in range(len(pts) - 1):
                lines.append((pts[i], pts[i + 1]))

    # 3. POLYLINE entities (2D / 3D polyline)
    for entity in msp.query('POLYLINE'):
        if entity.is_2d_polyline or entity.is_3d_polygon_mesh is False:
            pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
            if len(pts) < 2:
                continue
            if getattr(entity, 'is_closed', False):
                dedup = deduplicate_vertices(pts)
                if len(dedup) >= 3:
                    closed_polygons.append(dedup)
            else:
                for i in range(len(pts) - 1):
                    lines.append((pts[i], pts[i + 1]))

    return lines, closed_polygons


def parse_dxf_polygons(file_path, tol=1e-4):
    """
    Parses polygons from DXF file by reading LINE, LWPOLYLINE, and POLYLINE entities
    and assembling disconnected segments into closed loops using proximity snapping.
    """
    lines, closed_polygons = extract_segments_from_dxf(file_path)

    polygons = list(closed_polygons)
    remaining_lines = list(lines)

    while remaining_lines:
        polygon = []
        current_line = remaining_lines.pop(0)
        polygon.append(current_line[0])
        polygon.append(current_line[1])

        while True:
            for i, line in enumerate(remaining_lines):
                if is_close_point(line[0], polygon[-1], tol=tol):
                    polygon.append(line[1])
                    remaining_lines.pop(i)
                    break
                elif is_close_point(line[1], polygon[-1], tol=tol):
                    polygon.append(line[0])
                    remaining_lines.pop(i)
                    break
            else:
                break

        polygon = deduplicate_vertices(polygon, tol=tol)
        if len(polygon) >= 3:
            polygons.append(polygon)

    return polygons


def previous_and_next(some_iterable):
    """Returns iterator over (prev, curr, next) tuples."""
    prevs, items, nexts = tee(some_iterable, 3)
    prevs = chain([None], prevs)
    nexts = chain(islice(nexts, 1, None), [None])
    return zip(prevs, items, nexts)


def col_poly_is_up(polygon):
    if len(polygon) != 3:
        return False
    y_coords = [vertex[1] for vertex in polygon]
    y_coords.sort()
    return y_coords[1] < y_coords[2]


def col_poly_is_down(polygon):
    if len(polygon) != 3:
        return False
    y_coords = sorted([vertex[1] for vertex in polygon])
    return y_coords[0] < y_coords[1]


def row_poly_is_left(polygon):
    if len(polygon) != 3:
        return False
    x_coords = sorted([vertex[0] for vertex in polygon])
    return x_coords[0] < x_coords[1]


def row_poly_is_right(polygon):
    if len(polygon) != 3:
        return False
    x_coords = sorted([vertex[0] for vertex in polygon])
    return x_coords[1] < x_coords[2]


def calculate_center(polygon):
    if not polygon:
        return 0.0, 0.0
    x_coords = [vertex[0] for vertex in polygon]
    y_coords = [vertex[1] for vertex in polygon]
    center_x = sum(x_coords) / len(x_coords)
    center_y = sum(y_coords) / len(y_coords)
    return center_x, center_y


def group_polygons_by_center(polygons, axis='x', tolerance=0.1):
    """
    Group polygons by center coordinate along the specified axis ('x' or 'y').
    Clusters centers that are within `tolerance` mm of each other.
    """
    if not polygons:
        return {}

    axis_idx = 0 if axis == 'x' else 1
    items = []
    for poly in polygons:
        center = calculate_center(poly)
        items.append((center[axis_idx], poly))

    items.sort(key=lambda item: item[0])

    groups = defaultdict(list)
    if not items:
        return groups

    current_cluster_center = items[0][0]
    current_cluster_polys = [items[0][1]]
    cluster_centers = [current_cluster_center]

    for val, poly in items[1:]:
        # If within tolerance from the average/representative cluster center
        if abs(val - (sum(cluster_centers) / len(cluster_centers))) <= tolerance:
            current_cluster_polys.append(poly)
            cluster_centers.append(val)
        else:
            avg_center = round(sum(cluster_centers) / len(cluster_centers), 4)
            groups[avg_center].extend(current_cluster_polys)
            current_cluster_polys = [poly]
            cluster_centers = [val]

    if current_cluster_polys:
        avg_center = round(sum(cluster_centers) / len(cluster_centers), 4)
        groups[avg_center].extend(current_cluster_polys)

    return groups


def create_column(index, polygons, params=None):
    if params is None:
        params = {
            'polygonLineWidth': polygonLineWidth,
            'viaSize': viaSize,
            'viaDrill': viaDrill,
            'viaOffset': viaOffset,
            'traceWidth': traceWidth
        }

    p_line_width = params.get('polygonLineWidth', polygonLineWidth)
    v_size = params.get('viaSize', viaSize)
    v_drill = params.get('viaDrill', viaDrill)
    v_offset = params.get('viaOffset', viaOffset)
    t_width = params.get('traceWidth', traceWidth)

    signal = ET.Element('signal', name=f'COL{index}')
    center_x_first, center_y_first = calculate_center(polygons[0])
    pad_element = ET.Element('element', name=f'COL{index}', library="touchpad", package="PAD_0_5MM",
                             value="", x='{:.10f}'.format(center_x_first), y='{:.10f}'.format(center_y_first), smashed="yes")
    contactref_element = ET.Element('contactref', element=f'COL{index}', pad="P1")
    signal.append(contactref_element)

    for polygon_prev, polygon, polygon_next in previous_and_next(polygons):
        polygon_element = ET.Element('polygon', width='{:.10f}'.format(p_line_width), layer="1", pour="solid", thermals="no", isolate="0")
        
        for vertex in polygon:
            vertex_element = ET.Element('vertex', x='{:.10f}'.format(vertex[0]), y='{:.10f}'.format(vertex[1]))
            polygon_element.append(vertex_element)
        signal.append(polygon_element)
        center_x, center_y = calculate_center(polygon)
        
        y_coords = [vertex[1] for vertex in polygon]
        y_coords.sort()
        if polygon_next is not None:
            y_coords_next = [vertex[1] for vertex in polygon_next]
            y_coords_next.sort()

        if len(polygon) == 3:
            if col_poly_is_up(polygon):
                via_element = ET.Element('via', x='{:.10f}'.format(center_x), y='{:.10f}'.format(y_coords[2] - (v_size + v_offset)),
                                         extent="1-16", drill='{:.10f}'.format(v_drill), diameter='{:.10f}'.format(v_size))
                signal.append(via_element)
                if polygon_next is not None:
                    wire_element = ET.Element('wire', x1='{:.10f}'.format(center_x), y1='{:.10f}'.format(y_coords[2] - (v_size + v_offset)),
                                              x2='{:.10f}'.format(center_x), y2='{:.10f}'.format(y_coords_next[0] + (v_size + v_offset)),
                                              width='{:.10f}'.format(t_width), layer="16")
                    signal.append(wire_element)
            else:
                via_element = ET.Element('via', x='{:.10f}'.format(center_x), y='{:.10f}'.format(y_coords[0] + (v_size + v_offset)),
                                         extent="1-16", drill='{:.10f}'.format(v_drill), diameter='{:.10f}'.format(v_size))
                signal.append(via_element)
        elif len(polygon) == 4:
            # bottom via
            via_element_bottom = ET.Element('via', x='{:.10f}'.format(center_x), y='{:.10f}'.format(y_coords[0] + (v_size + v_offset)),
                                            extent="1-16", drill='{:.10f}'.format(v_drill), diameter='{:.10f}'.format(v_size))
            signal.append(via_element_bottom)
            # top via
            via_element_top = ET.Element('via', x='{:.10f}'.format(center_x), y='{:.10f}'.format(y_coords[3] - (v_size + v_offset)),
                                         extent="1-16", drill='{:.10f}'.format(v_drill), diameter='{:.10f}'.format(v_size))
            signal.append(via_element_top)
            # connect top via to the next polygon's bottom via
            if polygon_next is not None:
                wire_element = ET.Element('wire', x1='{:.10f}'.format(center_x), y1='{:.10f}'.format(y_coords[3] - (v_size + v_offset)),
                                          x2='{:.10f}'.format(center_x), y2='{:.10f}'.format(y_coords_next[0] + (v_size + v_offset)),
                                          width='{:.10f}'.format(t_width), layer="16")
                signal.append(wire_element)

    return signal, pad_element


def create_row(index, polygons, params=None):
    if params is None:
        params = {
            'polygonLineWidth': polygonLineWidth,
            'traceWidth': traceWidth
        }

    p_line_width = params.get('polygonLineWidth', polygonLineWidth)
    t_width = params.get('traceWidth', traceWidth)

    signal = ET.Element('signal', name=f'ROW{index}')
    center_x_first, center_y_first = calculate_center(polygons[0])
    pad_element = ET.Element('element', name=f'ROW{index}', library="touchpad", package="PAD_0_5MM",
                             value="", x='{:.10f}'.format(center_x_first), y='{:.10f}'.format(center_y_first), smashed="yes")
    contactref_element = ET.Element('contactref', element=f'ROW{index}', pad="P1")
    signal.append(contactref_element)

    for polygon_prev, polygon, polygon_next in previous_and_next(polygons):
        # isolate="0" added for KiCad compatibility
        polygon_element = ET.Element('polygon', width='{:.10f}'.format(p_line_width), layer="1", pour="solid", thermals="no", isolate="0")
        
        for vertex in polygon:
            vertex_element = ET.Element('vertex', x='{:.10f}'.format(vertex[0]), y='{:.10f}'.format(vertex[1]))
            polygon_element.append(vertex_element)
        signal.append(polygon_element)
        center_x, center_y = calculate_center(polygon)
        if polygon_next is not None:
            center_x_next, center_y_next = calculate_center(polygon_next)
            if not row_poly_is_left(polygon):
                # connect polygons on same layer, no vias for rows
                wire_element = ET.Element('wire', x1='{:.10f}'.format(center_x), y1='{:.10f}'.format(center_y),
                                          x2='{:.10f}'.format(center_x_next), y2='{:.10f}'.format(center_y_next),
                                          width='{:.10f}'.format(t_width), layer="1")
                signal.append(wire_element)

    return signal, pad_element


def generate_schematic(rows, columns, schematic_template=None, output_schematic=None):
    target_template = schematic_template or schematicFile
    target_output = output_schematic or outputSchematicFile

    try:
        tree = ET.parse(target_template)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Schematic template '{target_template}' not found.") from e

    root = tree.getroot()
    parts_element = root.find('.//parts')
    instances_element = root.find('.//instances')
    nets_element = root.find('.//nets')

    if parts_element is None:
        raise ValueError(f"Invalid schematic template '{target_template}': missing <parts>")
    if instances_element is None:
        raise ValueError(f"Invalid schematic template '{target_template}': missing <instances>")
    if nets_element is None:
        raise ValueError(f"Invalid schematic template '{target_template}': missing <nets>")

    for row in range(1, rows):
        part_element = ET.Element('part', name=f'ROW{row}', library="touchpad", deviceset="TESTPOINT", device="PAD_0_5MM")
        parts_element.append(part_element)
        instance_element = ET.Element('instance', part=f'ROW{row}', gate="G1", x=f"{45.72 + row * 2.54:.2f}", y="0.00", smashed="yes")
        attribute_element = ET.Element('attribute', name="NAME", x=f"{45.72 + row * 2.54:.2f}", y="6.0", size="1.27", layer="95", rot="R90", align="center-left")
        instance_element.append(attribute_element)
        instances_element.append(instance_element)
        net_element = ET.Element('net', {'name': f'ROW{row}', 'class': '0'})
        segment_element = ET.Element('segment')
        pinref_element = ET.Element('pinref', part=f'ROW{row}', gate="G1", pin="P1")
        segment_element.append(pinref_element)
        net_element.append(segment_element)
        nets_element.append(net_element)

    for column in range(1, columns):
        part_element = ET.Element('part', name=f'COL{column}', library="touchpad", deviceset="TESTPOINT", device="PAD_0_5MM")
        parts_element.append(part_element)
        instance_element = ET.Element('instance', part=f'COL{column}', gate="G1", x=f"{45.72 + column * 2.54:.2f}", y="20.32", smashed="yes")
        attribute_element = ET.Element('attribute', name="NAME", x=f"{45.72 + column * 2.54:.2f}", y="26.32", size="1.27", layer="95", rot="R90", align="center-left")
        instance_element.append(attribute_element)
        instances_element.append(instance_element)
        net_element = ET.Element('net', {'name': f'COL{column}', 'class': '0'})
        segment_element = ET.Element('segment')
        pinref_element = ET.Element('pinref', part=f'COL{column}', gate="G1", pin="P1")
        segment_element.append(pinref_element)
        net_element.append(segment_element)
        nets_element.append(net_element)

    tree.write(target_output)
    return tree


def generate_pcb(dxf_input, board_template, schematic_template, output_board, output_schematic, params=None):
    """
    Core functional entry point to process a DXF file and generate board & schematic files.
    """
    if params is None:
        params = {}

    v_size = params.get('via_size', viaSize)
    v_drill = params.get('via_drill', viaDrill)
    v_offset = params.get('via_offset', viaOffset)
    t_width = params.get('trace_width', traceWidth)
    p_line_width = params.get('polygon_line_width', polygonLineWidth)

    # Validate design parameters
    if v_size <= 0:
        raise ValueError(f"via_size must be positive, got {v_size}")
    if v_drill <= 0:
        raise ValueError(f"via_drill must be positive, got {v_drill}")
    if v_drill > v_size:
        raise ValueError(f"via_drill ({v_drill}) cannot be larger than via_size ({v_size})")
    if t_width <= 0:
        raise ValueError(f"trace_width must be positive, got {t_width}")
    if p_line_width <= 0:
        raise ValueError(f"polygon_line_width must be positive, got {p_line_width}")

    config_params = {
        'viaSize': v_size,
        'viaDrill': v_drill,
        'viaOffset': v_offset,
        'traceWidth': t_width,
        'polygonLineWidth': p_line_width,
    }

    try:
        tree = ET.parse(board_template)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Board template '{board_template}' not found.") from e

    root = tree.getroot()
    polygons = parse_dxf_polygons(dxf_input)
    print(f"Found {len(polygons)} polygons in the DXF file")

    columns = group_polygons_by_center(polygons, axis='x')
    rows = group_polygons_by_center(polygons, axis='y')

    sorted_columns = sorted(columns.items())
    num_cols = max(0, math.ceil(len(sorted_columns) / 2) - 1)
    print(f"Columns : {num_cols}")

    sorted_rows = sorted(rows.items())
    num_rows = max(0, math.ceil(len(sorted_rows) / 2) - 1)
    print(f"Rows : {num_rows}")

    generate_schematic(math.ceil(len(sorted_rows) / 2), math.ceil(len(sorted_columns) / 2),
                       schematic_template=schematic_template, output_schematic=output_schematic)

    signals_element = root.find('.//signals')
    components_element = root.find('.//elements')
    if signals_element is None:
        board_elem = root.find('.//board')
        if board_elem is None:
            raise ValueError(f"Invalid board template '{board_template}': missing <board>")
        signals_element = ET.SubElement(board_elem, 'signals')

    if components_element is None:
        board_elem = root.find('.//board')
        components_element = ET.SubElement(board_elem, 'elements')

    # Odd indices correspond to actual signal columns/rows
    for column_index, (column, col_polys) in enumerate(sorted_columns):
        col_polys.sort(key=lambda p: calculate_center(p)[1])
        if column_index % 2 == 1:
            signal_element, pad_element = create_column(math.ceil(column_index / 2), col_polys, params=config_params)
            signals_element.append(signal_element)
            components_element.append(pad_element)

    for row_index, (row, row_polys) in enumerate(sorted_rows):
        row_polys.sort(key=lambda p: calculate_center(p)[0])
        if row_index % 2 == 1:
            signal_element, pad_element = create_row(math.ceil(row_index / 2), row_polys, params=config_params)
            signals_element.append(signal_element)
            components_element.append(pad_element)

    tree.write(output_board)
    return tree


def main():
    global viaSize, viaDrill, viaOffset, traceWidth, polygonLineWidth
    global dxf_path, boardFile, schematicFile, outputBoardFile, outputSchematicFile

    parser = argparse.ArgumentParser(description='Generate Touchpad PCB files from DXF.')
    parser.add_argument('--dxf', default='touchpad.dxf', help='Input DXF file (default: touchpad.dxf)')
    parser.add_argument('--board-template', default='empty.brd', help='Board template file (default: empty.brd)')
    parser.add_argument('--schematic-template', default='empty.sch', help='Schematic template file (default: empty.sch)')
    parser.add_argument('--output-board', default='Touchpad.brd', help='Output Board file (default: Touchpad.brd)')
    parser.add_argument('--output-schematic', default='Touchpad.sch', help='Output Schematic file (default: Touchpad.sch)')
    
    parser.add_argument('--via-size', type=float, default=0.4, help='Total width of via with annular ring (mm) (default: 0.4)')
    parser.add_argument('--via-drill', type=float, default=0.3, help='Via drill diameter (mm) (default: 0.3)')
    parser.add_argument('--via-offset', type=float, default=0.1, help='Distance from diamond corner to via (mm) (default: 0.1)')
    parser.add_argument('--trace-width', type=float, default=0.1, help='Width of connecting traces (mm) (default: 0.1)')
    parser.add_argument('--polygon-line-width', type=float, default=0.005, help='Outer line width of polygon fills (mm) (default: 0.005)')

    args = parser.parse_args()

    dxf_path = args.dxf
    boardFile = args.board_template
    schematicFile = args.schematic_template
    outputBoardFile = args.output_board
    outputSchematicFile = args.output_schematic
    
    viaSize = args.via_size
    viaDrill = args.via_drill
    viaOffset = args.via_offset
    traceWidth = args.trace_width
    polygonLineWidth = args.polygon_line_width

    params = {
        'via_size': viaSize,
        'via_drill': viaDrill,
        'via_offset': viaOffset,
        'trace_width': traceWidth,
        'polygon_line_width': polygonLineWidth,
    }

    try:
        generate_pcb(
            dxf_input=dxf_path,
            board_template=boardFile,
            schematic_template=schematicFile,
            output_board=outputBoardFile,
            output_schematic=outputSchematicFile,
            params=params
        )
    except (FileNotFoundError, ValueError) as err:
        print(f"Error: {err}")
        exit(1)


if __name__ == "__main__":
    main()
