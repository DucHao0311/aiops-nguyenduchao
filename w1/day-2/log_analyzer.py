import sys, os, re
import pandas as pd
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

def parse_ts(line):
    m = re.match(r'^(\d{6})\s(\d{6})', line)
    if m:
        try: return pd.to_datetime('20'+m.group(1)+' '+m.group(2), format='%Y%m%d %H%M%S')
        except: pass
    m = re.search(r'(\d{4}-\d{2}-\d{2}-\d{2}\.\d{2}\.\d{2})', line)
    if m:
        try: return pd.to_datetime(m.group(1), format='%Y-%m-%d-%H.%M.%S')
        except: pass
    m = re.search(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})', line)
    if m:
        try: return pd.to_datetime(m.group(1))
        except: pass
    return pd.NaT

def analyze(log_file):
    if not os.path.exists(log_file):
        print(f'ERROR: {log_file} not found'); sys.exit(1)
    cfg = TemplateMinerConfig()
    cfg.drain_sim_th = 0.5
    cfg.drain_depth  = 5
    miner = TemplateMiner(config=cfg)
    records, total = [], 0
    with open(log_file, encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            total += 1
            res = miner.add_log_message(line)
            records.append({'timestamp': parse_ts(line),
                            'tid': f"T-{res['cluster_id']}"})
    df      = pd.DataFrame(records)
    n_tmpl  = len(miner.drain.clusters)
    tid_map = {f'T-{c.cluster_id}': c.get_template() for c in miner.drain.clusters}
    counts  = df['tid'].value_counts()
    print('='*62)
    print(f'LOG FILE        : {log_file}')
    print(f'Total lines     : {total}')
    print(f'Unique templates: {n_tmpl}')
    print()
    print('--- Top-5 Templates ---')
    for tid, cnt in counts.head(5).items():
        pct  = cnt/total*100
        tmpl = tid_map.get(tid,'?')[:65]
        print(f'  {tid:6s} | {cnt:5d} ({pct:5.1f}%) | {tmpl}')
    print()
    valid = df.dropna(subset=['timestamp'])
    if not valid.empty:
        cutoff = valid['timestamp'].max() - pd.Timedelta(hours=1)
        last   = valid[valid['timestamp'] >= cutoff]
        before = valid[valid['timestamp'] <  cutoff]
        lc = last['tid'].value_counts()
        bc = before['tid'].value_counts()
        dur = max((cutoff - valid['timestamp'].min()).total_seconds()/3600, 1)
        print('--- Templates spike trong 1 gio gan nhat (ratio>3x) ---')
        spikes = []
        for tid in set(lc.index):
            cl = lc.get(tid, 0)
            rb = bc.get(tid, 0) / dur
            if rb > 0 and cl/rb > 3:
                spikes.append((tid, cl, rb, cl/rb))
            elif rb == 0 and cl > 0:
                spikes.append((tid, cl, 0, float('inf')))
        spikes.sort(key=lambda x: -x[3])
        if spikes:
            for tid,cl,rb,rt in spikes[:5]:
                rt_s = f'{rt:.1f}x' if rt != float('inf') else 'NEW'
                print(f'  {tid:6s} | last_hr={cl} avg/hr={rb:.1f} ratio={rt_s} | {tid_map.get(tid,"?")[:55]}')
        else:
            print('  (khong co spike bat thuong)')
        print()
        new_tids = set(lc.index) - set(bc.index)
        print('--- New templates (chua xuat hien truoc 1 gio gan nhat) ---')
        if new_tids:
            for tid in sorted(new_tids):
                print(f'  {tid:6s} | {tid_map.get(tid,"?")[:65]}')
        else:
            print('  (khong co template moi)')
    else:
        print('(Khong co timestamp hop le)')
    print('='*62)
    return n_tmpl

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python log_analyzer.py <logfile>'); sys.exit(1)
    analyze(sys.argv[1])
