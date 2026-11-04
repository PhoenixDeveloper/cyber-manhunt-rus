"""Targeted UI changes selected by prefab path and localization key, never object ID."""
from pathlib import Path
import copy
import json
import struct

ROOT = Path(__file__).resolve().parent


def patch_layout(env):
    from UnityPy.helpers.TypeTreeNode import TypeTreeNode
    from UnityPy.helpers import TypeTreeHelper
    from UnityPy.streams import EndianBinaryReader
    nodes = TypeTreeNode.from_list(json.loads((ROOT / 'ui-text-typetree.json').read_text()))
    objects = {o.path_id: o for o in env.objects}
    gos = {i: o.read_typetree() for i, o in objects.items() if o.type.name == 'GameObject'}
    trs = {i: o.read_typetree() for i, o in objects.items() if o.type.name in ('Transform', 'RectTransform')}
    go2tr = {d['m_GameObject']['m_PathID']: i for i, d in trs.items()}
    changes = {}
    def path(go):
        names, seen = [], set()
        while go:
            if go in seen or go not in gos or go not in go2tr:
                raise ValueError('Unexpected prefab hierarchy')
            seen.add(go)
            names.insert(0, gos[go]['m_Name'])
            parent = trs[go2tr[go]]['m_Father']['m_PathID']
            go = trs.get(parent, {}).get('m_GameObject', {}).get('m_PathID')
        return '/'.join(names)
    def normalized(value):
        if isinstance(value, float): return struct.unpack('<f', struct.pack('<f', value))[0]
        if isinstance(value, dict): return {k: normalized(v) for k, v in value.items()}
        if isinstance(value, list): return [normalized(v) for v in value]
        return value
    def save(obj, original, revised, node=None, label=''):
        revised = normalized(revised)
        if original == revised:
            return
        raw = obj.get_raw_data()
        obj.save_typetree(original, nodes=node)
        if obj.data != raw:
            raise ValueError('UI type tree does not round-trip: ' + label)
        obj.save_typetree(revised, nodes=node)
        decoded = TypeTreeHelper.read_typetree(obj._get_typetree_node(node),
            EndianBinaryReader(obj.data, endian=obj.reader.endian), as_dict=True,
            assetsfile=obj.assets_file, byte_size=len(obj.data), check_read=True)
        if decoded != revised:
            raise ValueError('UI serialization mismatch: ' + label)
        changes[obj.path_id] = label
    def fit(font, minimum, maximum):
        font.update(m_BestFit=1, m_MinSize=minimum, m_MaxSize=maximum,
                    m_HorizontalOverflow=0, m_VerticalOverflow=0)
    keys = (b'^psw_', b'^sql01', b'^sql02', b'^copy', b'^open', b'^paste', b'^password', b'^txt_pwtitle',
            b'^tuili4007', b'^tuili0458', b'^tuili0459', b'^tuili0460', b'^tuili0461',
            b'^invade_phone0403', b'^twodrive_label06')
    for i, obj in objects.items():
        if obj.type.name != 'MonoBehaviour' or not any(k in obj.get_raw_data() for k in keys):
            continue
        try:
            old = obj.read_typetree(nodes=nodes)
        except Exception:
            continue
        if 'm_Text' not in old or old['m_GameObject']['m_FileID'] != 0:
            continue
        go = old['m_GameObject']['m_PathID']
        p = path(go)
        root = p.split('/')[0]
        password = root in ('passwordDialog', 'passwordDialog_dlc', 'passwordDialog_dlc7')
        database = root in ('sqlDialog', 'sqlDialog_dlc')
        note = root in ('noteitem', 'noteitem_dlc', 'noteitem_dlc7', 'noteitem1', 'noteitem3')
        coral = root == 'ReasoningPanel4007'
        phone_number = root == 'phonenumDialog'
        if not (password or database or note or coral or phone_number):
            continue
        value = copy.deepcopy(old)
        font = value['m_FontData']; key = value['m_Text']
        rect_id = go2tr[go]; rect = copy.deepcopy(trs[rect_id])
        if phone_number and p == 'phonenumDialog/bk/content/tiptxt' and key == '^invade_phone0403':
            # Keep the full hint below the four input boxes. Two lines need
            # more than the original 30 units; the panel is 464 units wide.
            fit(font, 18, 22)
            font['m_Alignment'] = 1  # upper center
            rect['m_SizeDelta'].update(x=400.0, y=72.0)
            rect['m_AnchoredPosition'].update(x=0.0, y=-76.0)
        elif phone_number and p == 'phonenumDialog/bk/content/txt_tip' and key == '^twodrive_label06':
            # Leave a separate row for the error above the OK button.
            rect['m_AnchoredPosition']['y'] = -132.0
        elif coral and '/step00/img_answer' in p and p.endswith('/Text') and key in {
                '^tuili4007' + str(n) for n in range(16, 26)}:
            # The 152x46 cards leave a 142x36 text area. Allow two complete
            # Russian lines instead of silently clipping the second line.
            fit(font, 14, 18)
        elif coral and '/step01/dialog' in p and p.endswith('/txt_content') and key in {
                '^tuili0458', '^tuili0459', '^tuili0460', '^tuili0461'}:
            # Fog images are intentional puzzle elements. Only fit the text
            # underneath; DragCloudItem still reveals it after a correct drop.
            fit(font, 14, 20)
        elif password and key.startswith('^psw_') and p.endswith('/title'):
            fit(font, 18, min(22, font['m_FontSize']))
        elif database and key in ('^sql01', '^sql02'):
            fit(font, 20, 24)
        elif password and key in ('^password', '^txt_pwtitle'):
            # Keep the original left edge; give the heading its real available space.
            width, height = (740.0, 50.0) if key == '^password' else (740.0, 30.0)
            rect['m_AnchoredPosition']['x'] += (width - rect['m_SizeDelta']['x']) * rect['m_Pivot']['x']
            rect['m_SizeDelta'].update(x=width, y=height)
            fit(font, 32 if key == '^password' else 18, font['m_FontSize'])
        elif ((note and key in ('^copy', '^open')) or
              ((password or database) and key == '^paste')) and '/btn_' in p:
            fit(font, 16, min(20, font['m_FontSize']))
            parent_id = rect['m_Father']['m_PathID']; parent = copy.deepcopy(trs[parent_id])
            width = 132.0 if key == '^copy' else 100.0
            # Keep each button's right edge; the dossier's action icons stay clear.
            if parent['m_AnchorMin']['x'] != parent['m_AnchorMax']['x']:
                raise ValueError('Unexpected stretched button: ' + p)
            parent['m_AnchoredPosition']['x'] -= (width-parent['m_SizeDelta']['x'])*(1-parent['m_Pivot']['x'])
            parent['m_SizeDelta']['x'] = width
            save(objects[parent_id], trs[parent_id], parent, label=p + ' [button]')
            trs[parent_id] = parent
            rect['m_SizeDelta']['x'] = -8.0
        save(obj, old, value, nodes, p)
        save(objects[rect_id], trs[rect_id], rect, label=p + ' [rect]')
        trs[rect_id] = rect
    return changes
