"""Bind the C2 diagnostic runner to the previously qualified candidate."""
import hashlib
import json

QUALIFIED = '2be1443254b8092e21693a11f50d5b3423db1ea4f4ca6a6ca372c59c15fef09c'
SOURCE = 'ca07159f3da4b670e4aeb878faef928e69bc1634'


def verify(binary, producer, provenance, revision):
    prior=json.loads(provenance.read_text())
    if (prior.get('schema')!='c2-local-hot-namespace-summary-v1' or prior.get('passed') is not True
            or prior.get('source_commit')!=SOURCE or revision!=SOURCE
            or prior.get('binary_sha256',{}).get('brewfs-c2-namespace-seqfix-01-linux-x86_64')!=QUALIFIED):
        raise ValueError('Use the qualified C2 read-control candidate and its summary')
    pins={}
    for name,path in (('brewfs',binary),('producer',producer)):
        with path.open('rb') as stream:
            pins[name]=hashlib.file_digest(stream,'sha256').hexdigest()
    if pins['brewfs']!=QUALIFIED:
        raise ValueError('C2 binary differs from the qualified artifact')
    return pins
