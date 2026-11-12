#!/usr/bin/env python3
"""Build the exact B C2 observer file map from already verified archives, offline."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile


CELL = '05357b5089cd679c-r1-juicefs'
FORMAL = 'c2-fa630431ea12-'+CELL
CAL = tuple('cal-pool-b-j-tr-c201-on'+str(n) for n in (1, 2, 3))
TAGS = (FORMAL, *CAL)
MANIFEST = '.suite-manifest.json'
PUBLIC = 'c2-jfs-01/publication-20261111-01/'
PINNED = {
    'candidates': ('preparation/b-object-readonly-inspection-01/coordinator/readonly-candidates-v2.stdout.json',
                   '5d5b0ce735a09c94e073010f68242bf207809552e8da5513d276e520d2897124'),
    'readback': (PUBLIC+'independent-readback.json',
                 '24812883499b37ca697da5f1ec29b8c7d53e382d15d697a452e17be0a92e6245'),
    'objects': (PUBLIC+'objects.tar.gz.json',
                '88904fe73a8cf42c6c2faedce2c4e3c93ce7c11d068a7b8a6b6148d126a08219'),
    'calibrations': (PUBLIC+'calibrations.tar.gz.json',
                     '3cd91856ea05fe9547890edbdb986f27ce773165145bcd3f940f779e32000827'),
}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(body):
    return hashlib.sha256(body).hexdigest()


def safe(name):
    p = PurePosixPath(name)
    return bool(name and not p.is_absolute() and str(p)==name and
                all(x not in name for x in ('\\', ':', '\0')) and '..' not in p.parts and p.parts)


def documents(evidence):
    docs, refs = {}, {}
    for key, (relative, expected) in PINNED.items():
        path = (evidence/relative).resolve(strict=True)
        require(path.is_relative_to(evidence), 'Pinned evidence escaped source checkout')
        body = path.read_bytes()
        require(sha(body)==expected, 'Pinned evidence changed: '+relative)
        docs[key], refs[key] = json.loads(body), dict(path=str(path), sha256=expected)
    return docs, refs


def archive_pin(file, docs):
    row = docs['readback']['objects'][file+'.tar.gz']
    receipt = docs[file]
    pub = receipt['upload']
    require(docs['readback']['passed'] is True and docs['readback']['anonymous_internal_get'] is True and
            row['verified']['passed'] is True and pub['passed'] is True and
            pub['full_internal_readback'] is True and receipt['anonymous_public_head'] is True and
            pub['sha256']==pub['readback_sha256']==row['sha256'] and pub['bytes']==row['bytes'],
            'Missing matching OSS full readback and anonymous access: '+file)
    return row


def archive_check(record, member, kind, depth):
    rows = [row['check'] for row in record['verified']['checks']
            if row['member']==member and row['depth']==depth]
    require(len(rows)==1, 'Missing exact independent member check: '+member)
    check = rows[0]
    require(check['schema']=='suite-archive-check-v1' and check['passed'] is True and
            check['cell_id']==CELL and check['kind']==kind and check['deferred']==[],
            'Unexpected independent member check: '+member)
    return check


def first_manifest(tar, check):
    member = tar.next()
    require(member and member.isfile() and member.name==MANIFEST and member.size<=16*1024**2,
            'Expected the original bounded, first-member manifest')
    with tar.extractfile(member) as stream:
        body = json.load(stream)
    files = body['files']
    require(body['schema']=='suite-archive-manifest-v1' and body['cell_id']==CELL and
            body['kind']==check['kind'] and body['deferred']==[] and len(files)==check['file_members'] and
            all(safe(name) and set(row)=={'bytes','sha256'} and type(row['bytes']) is int and
                row['bytes']>=0 and re.fullmatch('[0-9a-f]{64}',row['sha256']) for name,row in files.items()),
            'Archived member manifest differs from independent evidence')
    return files


def checked_archive(path, row, sink):
    # Hash the same open file we subsequently read; no trust in names alone.
    with path.open('rb') as source:
        require(hashlib.file_digest(source,'sha256').hexdigest()==row['sha256'] and
                source.tell()==row['bytes'], 'Archive differs from verified OSS bytes: '+str(path))
        source.seek(0)
        with tarfile.open(fileobj=source,mode='r:gz') as tar:
            return sink(tar)


def select_manifest(files, tag):
    prefix = 'o/'+tag+'/'
    return {name.removeprefix(prefix): row for name,row in files.items() if name.startswith(prefix)}


def proof(formal, calibrations, docs, refs):
    roles = {}
    files_by_tag = {}
    formal_pin = archive_pin('objects',docs)
    check = archive_check(formal_pin,'objects.tar.gz','objects',0)
    require((check['sha256'],check['bytes'])==(formal_pin['sha256'],formal_pin['bytes']),
            'Formal archive and independent receipt differ')
    files_by_tag[FORMAL] = checked_archive(formal,formal_pin,lambda tar:select_manifest(first_manifest(tar,check),FORMAL))
    roles[FORMAL] = dict(path=str(formal), sha256=formal_pin['sha256'])

    calibration_pin = archive_pin('calibrations',docs)
    outer = archive_check(calibration_pin,'calibrations.tar.gz','six-calibration-trials',0)
    require((outer['sha256'],outer['bytes'])==(calibration_pin['sha256'],calibration_pin['bytes']),
            'Outer archive and independent receipt differ')

    def nested(tar):
        files = first_manifest(tar,outer)
        wanted = {tag+'/objects.tar.gz':tag for tag in CAL}
        found = set()
        while member := tar.next():
            if member.name not in wanted:
                continue
            name = member.name
            tag = wanted[name]
            check = archive_check(calibration_pin,'calibrations.tar.gz!/'+name,'calibration-'+tag+'-objects',1)
            require(name not in found and member.isfile() and member.size==check['bytes'] and
                    files[name]==dict(bytes=check['bytes'],sha256=check['sha256']),
                    'Nested object archive differs: '+tag)
            with tar.extractfile(member) as stream:
                body = stream.read()
            require(len(body)==check['bytes'] and sha(body)==check['sha256'],
                    'Nested object archive SHA mismatch: '+tag)
            with tarfile.open(fileobj=io.BytesIO(body),mode='r:gz') as package:
                files_by_tag[tag] = select_manifest(first_manifest(package,check),tag)
            roles[tag] = dict(path=str(calibrations), outer_sha256=calibration_pin['sha256'],
                              member=name, sha256=check['sha256'])
            found.add(name)
        require(found==set(wanted), 'Missing one or more of the original C2 objects archives')

    checked_archive(calibrations,calibration_pin,nested)
    old = docs['candidates']
    require(old['serial']=='bp1bhdngko7nq3c2i340' and old['all_unmounted'] is True,
            'Unexpected original B objects-0 candidate census')
    selected = [row for row in old['groups'] if row['tag'] in TAGS]
    targets = {}
    for tag, files in files_by_tag.items():
        raw = 'o/'+tag
        staged = ('evidence/'+tag+'/'+raw if tag==FORMAL else
                  'retained-calibrations/pool-b-juicefs-c2-01/'+tag+'/raw/'+raw)
        matches = [row for row in selected if row['tag']==tag]
        require(len(matches)==2 and {row['relative_path'] for row in matches}=={raw,staged} and
                bool(files) and all(row['files']==len(files) for row in matches),
                'Historical candidate count differs from archived observer manifest: '+tag)
        targets[tag] = [raw,staged]
    return dict(schema='b-c2-observer-archive-proof-v1', files_by_tag=files_by_tag, targets_by_tag=targets,
                role_archives=roles, source_pins=refs, disk_serial=old['serial'],
                historical_allocated_bytes=sum(row['allocated_bytes'] for row in selected),
                historical_regular_files=sum(row['files'] for row in selected),
                scope='Archive-side expected files only; live path/owner/member and capacity checks still required',
                removal_authorized=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('objects','calibrations','out'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--evidence',type=Path,default=Path(__file__).resolve().parents[1]/'evidence')
    a = p.parse_args()
    docs, refs = documents(a.evidence.resolve(strict=True))
    value = proof(a.objects.resolve(strict=True),a.calibrations.resolve(strict=True),docs,refs)
    with a.out.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(value,stream,separators=(',',':'),sort_keys=True)
        stream.write('\n')
    print(json.dumps(dict(path=str(a.out.resolve(strict=True)),tags=len(value['files_by_tag']),
                          historical_allocated_bytes=value['historical_allocated_bytes'])))


if __name__=='__main__':
    main()
