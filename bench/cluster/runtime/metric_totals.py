"""Aggregate RustFS S3 payloads separately from internal HTTP traffic."""
import re


def rustfs_payload_metrics(metrics):
    totals={};methods={}
    for key,value in metrics.items():
        name=key.split('{',1)[0]
        totals[name]=totals.get(name,0)+value
        if name=='rustfs_s3_http_requests_total':
            match=re.search(r'(?:\{|,)method="([^"\n]+)"',key)
            method=match.group(1) if match else 'ALL'
            methods[method]=methods.get(method,0)+value
    def count(name):return int(round(totals.get(name,0)))
    return {
        'rustfs_s3_get_bytes':count('rustfs_s3_get_object_size_bytes_sum'),
        'rustfs_s3_put_bytes':count('rustfs_s3_put_object_size_bytes_sum'),
        'rustfs_s3_get_requests':count('rustfs_s3_get_object_total'),
        'rustfs_s3_put_requests':count('rustfs_s3_put_object_total'),
        'rustfs_s3_requests_by_method':{method:int(round(value)) for method,value in methods.items()},
        'rustfs_http_response_bytes':count('rustfs_http_server_response_body_bytes_total'),
        'rustfs_http_request_bytes':count('rustfs_http_server_request_body_bytes_total'),
        'rustfs_shard_read_bytes':count('rustfs_io_get_object_shard_read_observed_bytes_total'),
        'rustfs_quantized_requested_bytes':count('rustfs_io_get_object_quantized_range_requested_bytes_total'),
        'rustfs_quantized_quorum_window_bytes':count('rustfs_io_get_object_quantized_range_quorum_window_bytes_total'),
        'rustfs_internode_sent_bytes':count('rustfs_system_network_internode_sent_bytes_total'),
        'rustfs_internode_received_bytes':count('rustfs_system_network_internode_recv_bytes_total'),
    }
