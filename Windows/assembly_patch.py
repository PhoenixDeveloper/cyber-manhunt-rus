"""Change display strings only; retain PE size, methods and gameplay instructions."""
from functools import lru_cache
import struct


@lru_cache(maxsize=2)
def patch_assembly(source):
    import dnfile
    from dncil.cil.body import CilMethodBody
    from dncil.cil.body.reader import CilMethodBodyReaderBytes
    pe = dnfile.dnPE(data=source, fast_load=True, clr_lazy_load=True)
    pe.parse_data_directories(directories=[14])
    if not pe.net or not pe.net.user_strings:
        raise ValueError('Not a supported managed assembly')
    heap = pe.net.user_strings
    needed = {}
    index = 1
    while index < heap.struct.Size:
        if source[heap.file_offset + index] == 0:
            break  # stream padding
        item = heap.get(index)
        if item is None or not item.raw_size:
            raise ValueError('Invalid user string heap')
        text = str(item)
        if text in ('PASSWORD', ':', ': '):
            if text in needed:
                raise ValueError('Ambiguous display string: ' + text)
            needed[text] = (index, item)
        index += item.raw_size
    if set(needed) != {'PASSWORD', ':', ': '}:
        raise ValueError('Display strings differ from the supported game version')
    result = bytearray(source)
    changes = []
    index, item = needed['PASSWORD']
    pos = heap.file_offset + index + item.item_size.raw_size
    old = 'PASSWORD'.encode('utf-16le') + b'\x00'
    new = 'ПАРОЛЬ  '.encode('utf-16le') + b'\x01'
    if source[pos:pos+len(old)] != old or len(new) != len(old):
        raise ValueError('Unexpected PASSWORD storage')
    result[pos:pos+len(old)] = new
    changes.append({'offset': pos, 'before': old.hex(), 'after': new.hex(), 'display': 'PASSWORD → ПАРОЛЬ'})
    found = set()
    expected = {'InitContent', 'NoAnimation', '<Time_count>b__38_0'}
    for kind in pe.net.mdtables.TypeDef:
        if str(kind.TypeName) != 'NoteItem':
            continue
        for method in kind.MethodList:
            row = method.row; name = str(row.Name)
            if name not in expected:
                continue
            body = CilMethodBody(CilMethodBodyReaderBytes(pe.get_data(row.Rva)))
            matches = [ins for ins in body.instructions if ins.opcode.name == 'ldstr' and
                       ins.operand.value == 0x70000000 | needed[':'][0]]
            if len(matches) != 1:
                raise ValueError('Unexpected dossier display method: ' + name)
            ins = matches[0]; pos = pe.get_offset_from_rva(row.Rva) + ins.offset
            old = b'\x72' + struct.pack('<I', 0x70000000 | needed[':'][0])
            new = b'\x72' + struct.pack('<I', 0x70000000 | needed[': '][0])
            if source[pos:pos+5] != old:
                raise ValueError('Unexpected IL offset')
            result[pos:pos+5] = new
            changes.append({'offset': pos, 'before': old.hex(), 'after': new.hex(), 'display': 'NoteItem.' + name + ': colon spacing'})
            found.add(name)
    if found != expected:
        raise ValueError('Dossier methods not found')
    # All bytes outside four explicitly validated display edits remain unchanged.
    reverse = bytearray(result)
    for edit in changes:
        pos = edit['offset']; old = bytes.fromhex(edit['before'])
        reverse[pos:pos+len(old)] = old
    if bytes(reverse) != source or len(result) != len(source):
        raise ValueError('Unexpected assembly changes')
    pe.close()
    return bytes(result), changes
