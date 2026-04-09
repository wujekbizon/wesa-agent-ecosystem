const { useState, useEffect, useRef, useCallback } = React;
const { createRoot } = ReactDOM;

const API_BASE = 'http://localhost:8765/api';
const WS_URL = 'ws://localhost:8765/ws';

async function apiFetch(path, method = 'GET', body = null) {
    const options = { method };
    if (body) { options.headers = { 'Content-Type': 'application/json' }; options.body = JSON.stringify(body); }
    const response = await fetch(`${API_BASE}${path}`, options);
    if (!response.ok) {
        const errorData = await response.json().catch(() => ({ message: 'Unknown error' }));
        throw new Error(errorData.message || `API error: ${response.status} ${response.statusText}`);
    }
    return response.json();
}

function formatTimestamp(ts) { return ts ? new Date(ts).toLocaleString() : '—'; }

function formatDuration(created, completed) {
    if (!created) return '—';
    const s = Math.floor(((completed ? new Date(completed) : new Date()) - new Date(created)) / 1000);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

const getStatusClass = (s) => s ? s.toLowerCase().replace(/ /g, '_') : '';
const statusColor = (s) => ({ pending: 'var(--pending)', in_progress: 'var(--in-progress)', done: 'var(--done)', failed: 'var(--failed)' })[s] || 'var(--muted)';

let toastIdCounter = 0;
const Toast = ({ toasts, onRemoveToast }) => (
    <div className="toast-container">
        {toasts.map(t => (
            <div key={t.id} className={`toast ${t.type}`}>
                <div className="toast-message">{t.message}</div>
                <button className="toast-close-button" onClick={() => onRemoveToast(t.id)}>&times;</button>
            </div>
        ))}
    </div>
);

const TopBar = ({ tasks, daemonStatus, onRestartDaemon }) => {
    const c = tasks.reduce((a, t) => { a[t.status] = (a[t.status] || 0) + 1; return a; }, {});
    return (
        <div className="top-bar">
            <div className="top-bar-left">
                <span className="brand-name">WESA</span>
                <div className="brand-sep"></div>
                <span className="brand-sub">Agent Ecosystem</span>
            </div>
            <div className="top-bar-center">
                <div className="status-pill pending">PENDING: {c.pending || 0}</div>
                <div className="status-pill in-progress">ACTIVE: {c.in_progress || 0}</div>
                <div className="status-pill done">DONE: {c.done || 0}</div>
                <div className="status-pill failed">FAILED: {c.failed || 0}</div>
            </div>
            <div className="top-bar-right">
                <div className={`daemon-status-dot ${daemonStatus.running ? 'green' : 'red'}`}></div>
                <span className="daemon-pid">PID: {daemonStatus.pid || '—'}</span>
                <button onClick={onRestartDaemon}>Restart</button>
            </div>
        </div>
    );
};

const DaemonHealthPanel = ({ daemonStatus, tasks, addToast }) => {
    const running = daemonStatus.running;
    const c = tasks.reduce((a, t) => { a[t.status] = (a[t.status] || 0) + 1; return a; }, {});
    const [scheduler, setScheduler] = React.useState(null);

    React.useEffect(() => {
        apiFetch('/scheduler/status').then(setScheduler).catch(() => {});
    }, []);

    const toggleScheduler = async () => {
        if (!scheduler) return;
        const endpoint = scheduler.enabled ? '/scheduler/disable' : '/scheduler/enable';
        try {
            const data = await apiFetch(endpoint, 'POST');
            setScheduler(prev => ({ ...prev, enabled: data.enabled }));
            addToast(`Research scheduler ${data.enabled ? 'enabled' : 'disabled'}.`, 'info');
        } catch (err) {
            addToast('Scheduler toggle failed: ' + err.message, 'error');
        }
    };

    return (
        <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', background: running ? 'var(--surface)' : 'rgba(255,51,51,0.05)', flexShrink: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                <span style={{ fontFamily: 'var(--font-ui)', fontSize: '0.68em', fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--muted)' }}>Daemon Health</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    {scheduler !== null && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.62em', color: 'var(--muted)', letterSpacing: '0.08em', textTransform: 'uppercase' }}>RC Scheduler</span>
                            <button onClick={toggleScheduler} style={{
                                fontFamily: 'var(--font-mono)', fontSize: '0.62em', fontWeight: 700,
                                letterSpacing: '0.08em', textTransform: 'uppercase', cursor: 'pointer',
                                border: 'none', borderRadius: '2px', padding: '2px 8px',
                                background: scheduler.enabled ? 'var(--done)' : 'var(--surface2)',
                                color: scheduler.enabled ? '#000' : 'var(--muted)',
                                outline: scheduler.enabled ? 'none' : '1px solid var(--border)',
                            }}>
                                {scheduler.enabled ? '● ON' : '○ OFF'}
                            </button>
                        </div>
                    )}
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.72em', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: running ? 'var(--done)' : 'var(--failed)' }}>● {running ? 'RUNNING' : 'STOPPED'}</span>
                </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '10px' }}>
                {[
                    { label: 'PID',     value: daemonStatus.pid || '—', color: null },
                    { label: 'PENDING', value: c.pending || 0,          color: 'var(--pending)' },
                    { label: 'ACTIVE',  value: c.in_progress || 0,      color: 'var(--in-progress)' },
                    { label: 'DONE',    value: c.done || 0,             color: 'var(--done)' },
                    { label: 'FAILED',  value: c.failed || 0,           color: c.failed ? 'var(--failed)' : null },
                ].map(({ label, value, color }) => (
                    <div key={label}>
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: '0.62em', color: 'var(--muted)', letterSpacing: '0.08em', marginBottom: '3px' }}>{label}</div>
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: '1.05em', fontWeight: 700, color: color || 'var(--text)' }}>{value}</div>
                    </div>
                ))}
            </div>
        </div>
    );
};

const SORT_FIELDS = { STATUS: 'status', MODEL: 'model', CREATED: 'created_at' };

const TaskQueueTable = ({ tasks, selectedTaskId, onSelectTask }) => {
    const [filterToday, setFilterToday] = useState(true);
    const [sortField, setSortField] = useState('created_at');
    const [sortDir, setSortDir] = useState('desc');
    const today = new Date().toDateString();

    const handleSort = (field) => {
        if (sortField === field) {
            setSortDir(d => d === 'asc' ? 'desc' : 'asc');
        } else {
            setSortField(field);
            setSortDir('desc');
        }
    };

    const sorted = [...tasks].sort((a, b) => {
        let va = a[sortField] || '';
        let vb = b[sortField] || '';
        if (sortField === 'created_at') { return sortDir === 'asc' ? new Date(va) - new Date(vb) : new Date(vb) - new Date(va); }
        return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
    });

    const filtered = filterToday
        ? sorted.filter(t => new Date(t.created_at).toDateString() === today)
        : sorted;

    const thStyle = (field) => ({
        padding: '7px 12px', textAlign: 'left', fontWeight: 700, fontSize: '0.72em',
        letterSpacing: '0.1em', borderBottom: '2px solid var(--border)', whiteSpace: 'nowrap',
        color: sortField === field ? 'var(--text)' : 'var(--muted)',
        cursor: field ? 'pointer' : 'default', userSelect: 'none',
    });
    const sortIcon = (field) => sortField === field ? (sortDir === 'asc' ? ' ↑' : ' ↓') : '';

    return (
        <div style={{ display: 'flex', flexDirection: 'column', flexGrow: 1, overflow: 'hidden', minHeight: 0 }}>
            <div style={{ padding: '7px 14px', borderBottom: '1px solid var(--border)', display: 'flex', gap: '8px', alignItems: 'center', background: 'var(--surface2)', flexShrink: 0 }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.72em', color: 'var(--muted)', letterSpacing: '0.06em' }}>{filtered.length} TASK{filtered.length !== 1 ? 'S' : ''}</span>
                <button onClick={() => setFilterToday(f => !f)} style={{ fontSize: '0.68em', padding: '2px 8px', background: filterToday ? 'var(--done)' : 'var(--surface)', color: filterToday ? '#000' : 'var(--muted)', border: filterToday ? 'none' : '1px solid var(--border)', borderRadius: '1px', cursor: 'pointer', fontFamily: 'var(--font-mono)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
                    {filterToday ? 'Today' : 'All time'}
                </button>
            </div>
            <div style={{ overflowY: 'auto', flexGrow: 1 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-mono)', fontSize: '0.78em' }}>
                    <thead>
                        <tr style={{ background: 'var(--surface2)', position: 'sticky', top: 0, zIndex: 1 }}>
                            <th style={thStyle(null)}>ID</th>
                            <th style={thStyle(null)}>TYPE</th>
                            <th style={thStyle('model')} onClick={() => handleSort('model')}>MODEL{sortIcon('model')}</th>
                            <th style={thStyle('status')} onClick={() => handleSort('status')}>STATUS{sortIcon('status')}</th>
                            <th style={thStyle('created_at')} onClick={() => handleSort('created_at')}>CREATED{sortIcon('created_at')}</th>
                            <th style={thStyle(null)}>DURATION</th>
                        </tr>
                    </thead>
                    <tbody>
                        {filtered.length === 0 ? (
                            <tr><td colSpan={6} style={{ padding: '24px 12px', color: 'var(--muted)', textAlign: 'center', letterSpacing: '0.06em' }}>— no tasks —</td></tr>
                        ) : filtered.map(t => (
                            <tr key={t.id} onClick={() => onSelectTask(t.id)}
                                style={{ borderBottom: '1px solid var(--border)', borderLeft: t.id === selectedTaskId ? '3px solid var(--done)' : '3px solid transparent', background: t.id === selectedTaskId ? 'var(--selected-bg)' : 'transparent', cursor: 'pointer', transition: 'background 0.1s' }}
                                onMouseEnter={e => { if (t.id !== selectedTaskId) e.currentTarget.style.background = 'var(--hover)'; }}
                                onMouseLeave={e => { if (t.id !== selectedTaskId) e.currentTarget.style.background = 'transparent'; }}
                            >
                                <td style={{ padding: '8px 12px', color: 'var(--text)', fontWeight: 700 }}>#{t.id.substring(0,8)}</td>
                                <td style={{ padding: '8px 12px' }}><span style={{ background: 'var(--surface2)', border: '1px solid var(--border)', padding: '1px 5px', borderRadius: '1px', fontSize: '0.9em', letterSpacing: '0.04em', color: 'var(--muted)' }}>{t.type}</span></td>
                                <td style={{ padding: '8px 12px', color: 'var(--muted)' }}>{t.model || '—'}</td>
                                <td style={{ padding: '8px 12px' }}><span style={{ color: statusColor(t.status), fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', fontSize: '0.85em' }}>● {t.status.replace(/_/g,' ')}</span></td>
                                <td style={{ padding: '8px 12px', color: 'var(--muted)', whiteSpace: 'nowrap' }}>{formatTimestamp(t.created_at)}</td>
                                <td style={{ padding: '8px 12px', color: 'var(--muted)' }}>{formatDuration(t.created_at, t.completed_at)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
};

const TaskLogPanel = ({ detail, onDeleteTask }) => {
    const [tab, setTab] = useState('overview');
    const tabBtn = (name, label) => (
        <button onClick={() => setTab(name)} style={{ background: 'none', border: 'none', borderBottom: tab === name ? '2px solid var(--done)' : '2px solid transparent', color: tab === name ? 'var(--done)' : 'var(--muted)', padding: '9px 16px', fontSize: '0.72em', fontWeight: 700, letterSpacing: '0.1em', cursor: 'pointer', textTransform: 'uppercase', fontFamily: 'var(--font-ui)', marginBottom: '-1px', borderRadius: 0, transition: 'color 0.15s, border-color 0.15s' }}>{label}</button>
    );
    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', borderLeft: '1px solid var(--border)' }}>
            <div style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface)', flexShrink: 0, display: 'flex', alignItems: 'flex-end', padding: '0 4px' }}>
                {tabBtn('overview','Overview')}{tabBtn('result','Result')}{tabBtn('error','Error')}
            </div>
            {!detail ? (
                <div className="no-task-selected">— no task selected —</div>
            ) : (
                <div style={{ padding: '16px 20px', overflowY: 'auto', flexGrow: 1 }}>
                    {tab === 'overview' && <>
                        <h2 style={{ marginTop: 0, marginBottom: '10px', display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', gap: '8px', borderBottom: '1px solid var(--border)', paddingBottom: '10px' }}>
                            <span className="task-id-header">#{detail.id.substring(0,8)}</span>
                            <span className="task-type-tag">{detail.type}</span>
                            <span className={`task-status-header ${getStatusClass(detail.status)}`}>{detail.status.replace(/_/g,' ')}</span>
                        </h2>
                        <div className="task-meta-line">
                            <span>MODEL: {detail.model || '—'}</span>
                            <span>CREATED: {formatTimestamp(detail.created_at)}</span>
                            <span>COMPLETED: {formatTimestamp(detail.completed_at)}</span>
                        </div>
                        <div className="task-detail-section"><h3>Prompt</h3><pre className="task-detail-pre">{detail.prompt}</pre></div>
                    </>}
                    {tab === 'result' && <div className="task-detail-section">
                        <h3>Result</h3>
                        {detail.result ? <pre className="task-detail-pre">{detail.result}</pre> : <div style={{ color: 'var(--muted)', fontFamily: 'var(--font-mono)', fontSize: '0.82em' }}>No result yet.</div>}
                        {detail.report && <><h3 style={{ marginTop: '20px' }}>Report</h3><pre className="task-detail-pre">{detail.report}</pre></>}
                    </div>}
                    {tab === 'error' && <div className="task-detail-section">
                        <h3>Error</h3>
                        {detail.error ? <pre className="task-detail-pre error">{detail.error}</pre> : <div style={{ color: 'var(--muted)', fontFamily: 'var(--font-mono)', fontSize: '0.82em' }}>No errors.</div>}
                    </div>}
                    <div className="delete-button-container">
                        <button className="btn-delete" onClick={() => onDeleteTask(detail.id)}>Delete Task</button>
                    </div>
                </div>
            )}
        </div>
    );
};

const DesignsPanel = ({ addToast }) => {
    const [designs, setDesigns] = React.useState([]);
    const [selectedDesign, setSelectedDesign] = React.useState(null);
    const [loading, setLoading] = React.useState(true);
    const [actionInProgress, setActionInProgress] = React.useState(null);
    const [retryFeedback, setRetryFeedback] = React.useState('');
    const [showRetryInput, setShowRetryInput] = React.useState(null);
    const hasInitialSelection = React.useRef(false);

    const fetchDesigns = React.useCallback(async () => {
        try {
            const data = await apiFetch('/designs');
            setDesigns(data);
            if (data.length > 0 && !hasInitialSelection.current) { hasInitialSelection.current = true; setSelectedDesign(data[0]); }
        } catch (err) { addToast('Failed to load designs: ' + err.message, 'error'); }
        finally { setLoading(false); }
    }, [addToast]);

    React.useEffect(() => { fetchDesigns(); const i = setInterval(fetchDesigns, 5000); return () => clearInterval(i); }, [fetchDesigns]);

    const handleApprove = React.useCallback(async (design) => {
        if (!window.confirm(`Approve "${design.name}"?\n\nThis will queue UX1 polish + FE1 plan + Q1 review.\nOther concepts remain available for approval.`)) return;
        setActionInProgress(design.filename);
        try {
            const result = await apiFetch('/designs/approve', 'POST', { filename: design.filename });
            addToast(`Approved "${design.name}". Tasks queued: polish ${result.polish_task}, FE1 ${result.fe1_task}, Q1 ${result.q1_review_task}.`, 'success');
            hasInitialSelection.current = false; await fetchDesigns();
        } catch (err) { addToast('Approve failed: ' + err.message, 'error'); }
        finally { setActionInProgress(null); }
    }, [addToast, fetchDesigns]);

    const handleRetry = React.useCallback(async (design) => {
        setActionInProgress(design.filename); setShowRetryInput(null);
        try {
            const result = await apiFetch('/designs/retry', 'POST', { filename: design.filename, feedback: retryFeedback });
            addToast(`Retry queued for "${design.name}" → task ${result.retry_task}.`, 'success');
            setRetryFeedback(''); setDesigns(prev => prev.filter(d => d.filename !== design.filename));
            if (selectedDesign && selectedDesign.filename === design.filename) setSelectedDesign(null);
        } catch (err) { addToast('Retry failed: ' + err.message, 'error'); }
        finally { setActionInProgress(null); }
    }, [addToast, retryFeedback, selectedDesign]);

    const handleDelete = React.useCallback(async (design) => {
        if (!window.confirm(`Dismiss "${design.name}"? File will be deleted.`)) return;
        try {
            await apiFetch('/designs/delete', 'POST', { filename: design.filename });
            setDesigns(prev => prev.filter(d => d.filename !== design.filename));
            if (selectedDesign && selectedDesign.filename === design.filename) setSelectedDesign(null);
            addToast(`Dismissed "${design.name}".`, 'info');
        } catch (err) { addToast('Delete failed: ' + err.message, 'error'); }
    }, [addToast, selectedDesign]);

    if (loading) return <div className="designs-empty"><span>Loading designs...</span></div>;
    const isConcept  = f => (f.filename.startsWith('concept-') || f.filename.endsWith('_prototype.html')) && !f.filename.startsWith('approved-') && !f.filename.startsWith('polished-');
    const isApproved = f => f.filename.startsWith('approved-');
    const isPolished = f => f.filename.startsWith('polished-');

    return (
        <div className="designs-panel">
            <div className="designs-list">
                <div className="designs-list-header">Designs ({designs.length})</div>
                {designs.length === 0 && <div style={{ padding: '14px', fontSize: '0.78em', color: 'var(--muted)', fontFamily: 'var(--font-mono)', letterSpacing: '0.04em' }}>No designs yet.</div>}
                {designs.map(d => (
                    <div key={d.filename}>
                        <div className={`designs-list-item ${selectedDesign && selectedDesign.filename === d.filename ? 'selected' : ''}`} onClick={() => setSelectedDesign(d)}>
                            {isApproved(d) && <span style={{ color: 'var(--done)', marginRight: '5px' }}>✓</span>}
                            {isPolished(d) && <span style={{ color: 'var(--in-progress)', marginRight: '5px' }}>★</span>}
                            {d.name}
                        </div>
                        {isConcept(d) && (
                            <div style={{ padding: '0 10px 8px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                <div style={{ display: 'flex', gap: '4px' }}>
                                    <button onClick={() => handleApprove(d)} disabled={actionInProgress === d.filename} style={{ flex:1, fontSize:'0.68em', padding:'4px 6px', background:'transparent', color:'var(--done)', border:'1px solid var(--done)', borderRadius:'1px', cursor:'pointer', opacity: actionInProgress===d.filename?0.4:1, fontFamily:'var(--font-mono)', letterSpacing:'0.06em', textTransform:'uppercase' }}>{actionInProgress===d.filename?'...':'Approve'}</button>
                                    <button onClick={() => setShowRetryInput(showRetryInput===d.filename?null:d.filename)} disabled={actionInProgress===d.filename} style={{ flex:1, fontSize:'0.68em', padding:'4px 6px', background:'transparent', color:'var(--failed)', border:'1px solid var(--failed)', borderRadius:'1px', cursor:'pointer', opacity: actionInProgress===d.filename?0.4:1, fontFamily:'var(--font-mono)', letterSpacing:'0.06em', textTransform:'uppercase' }}>Retry</button>
                                    <button onClick={() => handleDelete(d)} disabled={actionInProgress===d.filename} style={{ fontSize:'0.68em', padding:'4px 6px', background:'transparent', color:'var(--muted)', border:'1px solid var(--border)', borderRadius:'1px', cursor:'pointer', fontFamily:'var(--font-mono)' }}>✕</button>
                                </div>
                                {showRetryInput === d.filename && (
                                    <div style={{ display:'flex', flexDirection:'column', gap:'4px' }}>
                                        <textarea placeholder="Feedback for retry (optional)..." value={retryFeedback} onChange={e => setRetryFeedback(e.target.value)} rows={2} style={{ fontSize:'0.72em', padding:'5px 6px', background:'var(--bg)', color:'var(--text)', border:'1px solid var(--border)', borderRadius:'1px', resize:'none', fontFamily:'var(--font-mono)' }} />
                                        <button onClick={() => handleRetry(d)} style={{ fontSize:'0.68em', padding:'4px 6px', background:'transparent', color:'var(--in-progress)', border:'1px solid var(--in-progress)', borderRadius:'1px', cursor:'pointer', fontFamily:'var(--font-mono)', letterSpacing:'0.06em', textTransform:'uppercase' }}>Send Retry</button>
                                    </div>
                                )}
                            </div>
                        )}
                        {isApproved(d) && <div style={{ padding:'0 10px 8px', fontSize:'0.7em', color:'var(--done)', fontFamily:'var(--font-mono)', letterSpacing:'0.04em' }}>Approved — polish + FE1 + Q1 queued</div>}
                        {isPolished(d) && <div style={{ padding:'0 10px 8px', fontSize:'0.7em', color:'var(--muted)', fontFamily:'var(--font-mono)', letterSpacing:'0.04em' }}>Polished</div>}
                    </div>
                ))}
                <div style={{ padding: '10px', borderTop: '1px solid var(--border)' }}>
                    <button onClick={fetchDesigns} style={{ width: '100%', fontSize: '0.72em' }}>Refresh</button>
                </div>
            </div>
            <div className="designs-preview">
                {selectedDesign ? (<>
                    <div className="designs-preview-toolbar">
                        <span style={{ color:'var(--text)', fontWeight:600 }}>{selectedDesign.name}</span>
                        {isApproved(selectedDesign) && <span style={{ color:'var(--done)' }}>✓ Approved</span>}
                        {isPolished(selectedDesign) && <span style={{ color:'var(--in-progress)' }}>★ Polished</span>}
                        <a href={selectedDesign.url} target="_blank" rel="noopener noreferrer" style={{ marginLeft:'auto', color:'var(--in-progress)', textDecoration:'none', fontSize:'0.8em', fontFamily:'var(--font-mono)', letterSpacing:'0.04em' }}>Open in new tab →</a>
                    </div>
                    <iframe key={selectedDesign.filename} src={selectedDesign.url} title={selectedDesign.name} />
                </>) : <div className="designs-empty"><span>— select a concept to preview —</span></div>}
            </div>
        </div>
    );
};

const ReviewsPanel = () => {
    const [reviews, setReviews] = React.useState([]);
    const [selected, setSelected] = React.useState(null);
    const [content, setContent] = React.useState('');
    const [loading, setLoading] = React.useState(true);

    React.useEffect(() => { apiFetch('/reviews').then(d => { setReviews(d); setLoading(false); }).catch(() => setLoading(false)); }, []);

    const selectReview = async (r) => {
        setSelected(r); setContent('Loading...');
        try { const d = await apiFetch(`/reviews/${r.filename}`); setContent(d.content); }
        catch (e) { setContent('Failed to load review.'); }
    };

    if (loading) return <div style={{ padding:'24px', color:'var(--muted)', fontFamily:'var(--font-mono)', fontSize:'0.82em', letterSpacing:'0.06em' }}>Loading reviews...</div>;

    return (
        <div style={{ display:'flex', height:'100%', overflow:'hidden' }}>
            <div style={{ width:'240px', borderRight:'1px solid var(--border)', overflowY:'auto', flexShrink:0, background:'var(--surface)' }}>
                <div style={{ padding:'8px 14px', fontSize:'0.68em', fontWeight:700, color:'var(--muted)', borderBottom:'1px solid var(--border)', textTransform:'uppercase', letterSpacing:'0.1em', background:'var(--surface2)' }}>Q1 Reviews ({reviews.length})</div>
                {reviews.length === 0 && <div style={{ padding:'16px 14px', fontSize:'0.78em', color:'var(--muted)', fontFamily:'var(--font-mono)' }}>No reviews yet.</div>}
                {reviews.map(r => (
                    <div key={r.filename} onClick={() => selectReview(r)} style={{ padding:'10px 14px', cursor:'pointer', borderBottom:'1px solid var(--border)', background: selected&&selected.filename===r.filename?'var(--selected-bg)':'transparent', borderLeft: selected&&selected.filename===r.filename?'3px solid var(--in-progress)':'3px solid transparent', fontSize:'0.8em' }}>
                        <div style={{ fontFamily:'var(--font-mono)', fontSize:'0.85em', fontWeight:700, color: r.type==='rejection'?'var(--failed)':'var(--done)', marginBottom:'3px', letterSpacing:'0.06em' }}>{r.type==='rejection'?'✕ Rejected':'✓ Approved'}</div>
                        <div style={{ color:'var(--text)', wordBreak:'break-all', fontWeight:500 }}>{r.concept}</div>
                    </div>
                ))}
            </div>
            <div style={{ flex:1, overflowY:'auto', padding:'18px 24px' }}>
                {content ? <pre style={{ fontFamily:'var(--font-mono)', fontSize:'0.78em', color:'var(--text)', whiteSpace:'pre-wrap', lineHeight:1.7, margin:0 }}>{content}</pre>
                         : <div style={{ color:'var(--muted)', fontFamily:'var(--font-mono)', fontSize:'0.82em', letterSpacing:'0.06em' }}>— select a review to read it —</div>}
            </div>
        </div>
    );
};

// ── RC score badge colours ─────────────────────────────────────────────────
const rtColor = (score) => {
    if (score === null || score === undefined) return 'var(--muted)';
    if (score >= 80) return 'var(--done)';
    if (score >= 60) return 'var(--in-progress)';
    if (score >= 40) return 'var(--pending)';
    return 'var(--failed)';
};

const decisionColor = (d) => ({
    approve:  'var(--done)',
    discard:  'var(--failed)',
    hold:     'var(--pending)',
    pending:  'var(--muted)',
})[d?.toLowerCase()] || 'var(--muted)';

const ResearchCenterPanel = ({ addToast }) => {
    const [briefs,    setBriefs]    = React.useState([]);
    const [staging,   setStaging]   = React.useState([]);
    const [meta,      setMeta]      = React.useState(null);          // { last_sc1, last_sy1, topics[] }
    const [selected,  setSelected]  = React.useState(null);          // full brief object
    const [briefView, setBriefView] = React.useState('synthesis');   // 'synthesis'|'research'|'score'
    const [loading,   setLoading]   = React.useState(true);
    const [running,   setRunning]   = React.useState(false);

    const load = React.useCallback(() => {
        Promise.all([
            apiFetch('/research/briefs').catch(() => []),
            apiFetch('/research/staging').catch(() => []),
            apiFetch('/research/meta').catch(() => null),
        ]).then(([b, s, m]) => {
            setBriefs(b);
            setStaging(s);
            setMeta(m);
            setLoading(false);
        });
    }, []);

    React.useEffect(() => { load(); const i = setInterval(load, 8000); return () => clearInterval(i); }, [load]);

    const handleTrigger = async () => {
        setRunning(true);
        try {
            const r = await apiFetch('/research/trigger', 'POST');
            addToast(`RC pipeline queued — task: ${r.task_id}`, 'success');
        } catch (err) {
            addToast('Trigger failed: ' + err.message, 'error');
        } finally {
            setRunning(false);
        }
    };

    const handleDecision = async (briefId, decision) => {
        try {
            await apiFetch('/research/decision', 'POST', { brief_id: briefId, decision });
            addToast(`Brief ${briefId.substring(0,8)} marked: ${decision}`, 'info');
            load();
            if (selected && selected.id === briefId) setSelected(prev => ({ ...prev, decision }));
        } catch (err) {
            addToast('Decision failed: ' + err.message, 'error');
        }
    };

    // counts
    const counts = briefs.reduce((a, b) => {
        const k = b.decision || 'pending';
        a[k] = (a[k] || 0) + 1;
        return a;
    }, {});
    const stagingCount = staging.length;

    const selectBrief = async (brief) => {
        if (selected && selected.id === brief.id) { setSelected(null); return; }
        setSelected({ ...brief, _loading: true });
        try {
            const full = await apiFetch(`/research/briefs/${brief.id}`);
            setSelected(full);
        } catch {
            setSelected({ ...brief, _error: true });
        }
    };

    const scoreBar = (score) => {
        if (score === null || score === undefined) return null;
        const pct = Math.min(100, Math.max(0, score));
        return (
            <div style={{ height: '3px', background: 'var(--surface2)', borderRadius: '2px', marginTop: '4px', width: '100%' }}>
                <div style={{ height: '100%', width: `${pct}%`, background: rtColor(score), borderRadius: '2px', transition: 'width 0.4s' }} />
            </div>
        );
    };

    if (loading) return <div className="designs-empty"><span>Loading Research Center...</span></div>;

    return (
        <div className="rc-layout">

            {/* ── LEFT SIDEBAR ── */}
            <div className="rc-sidebar">

                {/* Queue stats */}
                <div className="rc-sidebar-section">
                    <div className="rc-section-label">Queue Status</div>
                    <div className="rc-stat-grid">
                        {[
                            { label: 'Staging',  value: stagingCount,          color: 'var(--in-progress)' },
                            { label: 'Pending',  value: counts.pending  || 0,  color: 'var(--pending)' },
                            { label: 'Approved', value: counts.approve  || 0,  color: 'var(--done)' },
                            { label: 'Discarded',value: counts.discard  || 0,  color: 'var(--failed)' },
                        ].map(({ label, value, color }) => (
                            <div key={label} className="rc-stat-cell">
                                <div className="rc-stat-value" style={{ color }}>{value}</div>
                                <div className="rc-stat-label">{label}</div>
                            </div>
                        ))}
                    </div>
                </div>

                {/* Scheduler meta */}
                {meta && (
                    <div className="rc-sidebar-section">
                        <div className="rc-section-label">Scheduler</div>
                        <div className="rc-meta-line"><span>SC1 last run</span><span>{meta.last_sc1 ? formatTimestamp(meta.last_sc1) : '—'}</span></div>
                        <div className="rc-meta-line"><span>SY1 last run</span><span>{meta.last_sy1 ? formatTimestamp(meta.last_sy1) : '—'}</span></div>
                        {meta.topics && meta.topics.length > 0 && <>
                            <div className="rc-section-label" style={{ marginTop: '10px' }}>Topic Rotation</div>
                            {meta.topics.map((t, i) => (
                                <div key={i} className="rc-topic-item">
                                    <span style={{ color: t.active ? 'var(--done)' : 'var(--muted)', marginRight: '6px' }}>{t.active ? '▶' : '·'}</span>
                                    <span style={{ color: t.active ? 'var(--text)' : 'var(--muted)' }}>{t.label}</span>
                                </div>
                            ))}
                        </>}
                    </div>
                )}

                {/* Trigger button */}
                <div className="rc-sidebar-section">
                    <button
                        onClick={handleTrigger}
                        disabled={running}
                        className="btn-primary"
                        style={{ width: '100%', padding: '10px 16px', fontSize: '0.72em' }}
                    >
                        {running ? 'Queuing...' : '▶ Trigger RC Run'}
                    </button>
                    <button onClick={load} style={{ width: '100%', marginTop: '6px', fontSize: '0.68em', padding: '6px' }}>Refresh</button>
                </div>

                {/* Staging queue */}
                {staging.length > 0 && (
                    <div className="rc-sidebar-section">
                        <div className="rc-section-label">Staging Queue ({staging.length})</div>
                        {staging.map(s => (
                            <div key={s.id || s.topic} className="rc-staging-item">
                                <span style={{ color: 'var(--in-progress)', marginRight: '5px' }}>◈</span>
                                <span style={{ fontSize: '0.75em', color: 'var(--text)', fontFamily: 'var(--font-mono)' }}>{s.topic || s.id}</span>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* ── BRIEF LIST ── */}
            <div className="rc-brief-list">
                <div className="rc-list-header">Research Briefs ({briefs.length})</div>
                {briefs.length === 0 && (
                    <div style={{ padding: '20px 14px', color: 'var(--muted)', fontSize: '0.78em', fontFamily: 'var(--font-mono)', letterSpacing: '0.04em' }}>
                        No briefs yet. Trigger a RC run to generate the first brief.
                    </div>
                )}
                {briefs.map(b => {
                    const isSelected = selected && selected.id === b.id;
                    return (
                        <div
                            key={b.id}
                            className={`rc-brief-item ${isSelected ? 'active' : ''}`}
                            onClick={() => selectBrief(b)}
                        >
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '6px' }}>
                                <span className="rc-brief-topic">{b.topic || b.id.substring(0, 20)}</span>
                                {b.rt1_score !== null && b.rt1_score !== undefined && (
                                    <span className="rc-score-badge" style={{ color: rtColor(b.rt1_score), borderColor: rtColor(b.rt1_score) }}>
                                        {b.rt1_score}
                                    </span>
                                )}
                            </div>
                            {scoreBar(b.rt1_score)}
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '5px' }}>
                                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.65em', color: 'var(--muted)', letterSpacing: '0.04em' }}>
                                    {formatTimestamp(b.created_at)}
                                </span>
                                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.65em', fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: decisionColor(b.decision) }}>
                                    {b.decision || 'pending'}
                                </span>
                            </div>
                        </div>
                    );
                })}
            </div>

            {/* ── DETAIL PANE ── */}
            <div className="rc-detail">
                {!selected ? (
                    <div className="no-task-selected">— select a brief to inspect —</div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>

                        {/* Detail tabs */}
                        <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', background: 'var(--surface)', flexShrink: 0, padding: '0 4px', alignItems: 'flex-end', justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex' }}>
                                {[
                                    { key: 'synthesis', label: 'Synthesis' },
                                    { key: 'research',  label: 'R1 Research' },
                                    { key: 'score',     label: 'RT1 Score' },
                                ].map(({ key, label }) => (
                                    <button
                                        key={key}
                                        onClick={() => setBriefView(key)}
                                        style={{
                                            background: 'none', border: 'none',
                                            borderBottom: briefView === key ? '2px solid var(--done)' : '2px solid transparent',
                                            color: briefView === key ? 'var(--done)' : 'var(--muted)',
                                            padding: '9px 16px', fontSize: '0.72em', fontWeight: 700,
                                            letterSpacing: '0.1em', cursor: 'pointer', textTransform: 'uppercase',
                                            fontFamily: 'var(--font-ui)', marginBottom: '-1px', borderRadius: 0,
                                        }}
                                    >{label}</button>
                                ))}
                            </div>

                            {/* Decision buttons */}
                            {!selected._loading && !selected._error && (
                                <div style={{ display: 'flex', gap: '6px', padding: '6px 8px' }}>
                                    {['approve', 'hold', 'discard'].map(d => (
                                        <button
                                            key={d}
                                            onClick={() => handleDecision(selected.id, d)}
                                            style={{
                                                fontSize: '0.62em', padding: '3px 10px',
                                                background: (selected.decision === d) ? decisionColor(d) : 'transparent',
                                                color: (selected.decision === d) ? '#000' : decisionColor(d),
                                                border: `1px solid ${decisionColor(d)}`,
                                                borderRadius: '1px', cursor: 'pointer',
                                                fontFamily: 'var(--font-mono)', letterSpacing: '0.08em', textTransform: 'uppercase',
                                            }}
                                        >{d}</button>
                                    ))}
                                </div>
                            )}
                        </div>

                        {/* Brief header */}
                        <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)', background: 'var(--surface)', flexShrink: 0 }}>
                            <div style={{ display: 'flex', alignItems: 'baseline', gap: '10px', flexWrap: 'wrap' }}>
                                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.7em', color: 'var(--muted)', letterSpacing: '0.04em' }}>#{selected.id ? selected.id.substring(0,8) : '—'}</span>
                                <span style={{ fontFamily: 'var(--font-ui)', fontSize: '0.88em', fontWeight: 700, color: 'var(--text)' }}>{selected.topic || '—'}</span>
                                {selected.rt1_score !== null && selected.rt1_score !== undefined && (
                                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8em', fontWeight: 700, color: rtColor(selected.rt1_score), letterSpacing: '0.06em' }}>
                                        RT1: {selected.rt1_score}/100
                                    </span>
                                )}
                                <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: '0.65em', color: 'var(--muted)' }}>{formatTimestamp(selected.created_at)}</span>
                            </div>
                        </div>

                        {/* Content */}
                        <div style={{ flex: 1, overflowY: 'auto', padding: '18px 22px' }}>
                            {selected._loading && <div style={{ color: 'var(--muted)', fontFamily: 'var(--font-mono)', fontSize: '0.82em', letterSpacing: '0.06em' }}>Loading brief...</div>}
                            {selected._error  && <div style={{ color: 'var(--failed)', fontFamily: 'var(--font-mono)', fontSize: '0.82em' }}>Failed to load brief content.</div>}
                            {!selected._loading && !selected._error && <>
                                {briefView === 'synthesis' && (
                                    <pre className="task-detail-pre" style={{ maxHeight: 'none' }}>
                                        {selected.synthesis || selected.w1_output || '— no synthesis content —'}
                                    </pre>
                                )}
                                {briefView === 'research' && (
                                    <pre className="task-detail-pre" style={{ maxHeight: 'none' }}>
                                        {selected.research || selected.r1_output || '— no R1 research content —'}
                                    </pre>
                                )}
                                {briefView === 'score' && (
                                    <div>
                                        {(selected.rt1_score !== null && selected.rt1_score !== undefined) ? (<>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '16px' }}>
                                                <div style={{ fontFamily: 'var(--font-mono)', fontSize: '2.2em', fontWeight: 700, color: rtColor(selected.rt1_score) }}>
                                                    {selected.rt1_score}
                                                </div>
                                                <div>
                                                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: '0.7em', color: 'var(--muted)', letterSpacing: '0.08em', textTransform: 'uppercase' }}>RT1 Relevance Score</div>
                                                    <div style={{ height: '6px', width: '200px', background: 'var(--surface2)', borderRadius: '3px', marginTop: '6px' }}>
                                                        <div style={{ height: '100%', width: `${selected.rt1_score}%`, background: rtColor(selected.rt1_score), borderRadius: '3px', transition: 'width 0.4s' }} />
                                                    </div>
                                                </div>
                                            </div>
                                            {selected.rt1_rationale && (
                                                <pre className="task-detail-pre" style={{ maxHeight: 'none' }}>{selected.rt1_rationale}</pre>
                                            )}
                                        </>) : (
                                            <div style={{ color: 'var(--muted)', fontFamily: 'var(--font-mono)', fontSize: '0.82em', letterSpacing: '0.06em' }}>— RT1 score not yet available —</div>
                                        )}
                                    </div>
                                )}
                            </>}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};

const WolfmedPanel = ({ addToast }) => {
    const [stats, setStats] = React.useState(null);
    const [books, setBooks] = React.useState([]);
    const [selectedBook, setSelectedBook] = React.useState(null);
    const [bookDetail, setBookDetail] = React.useState(null);
    const [loading, setLoading] = React.useState(true);
    const [detailLoading, setDetailLoading] = React.useState(false);

    const fetchBooks = React.useCallback(async () => {
        try {
            const [statsData, booksData] = await Promise.all([
                apiFetch('/wolfmed/stats'),
                apiFetch('/wolfmed/books'),
            ]);
            setStats(statsData);
            setBooks(booksData);
        } catch (err) {
            if (loading) addToast('Failed to load Wolfmed data: ' + err.message, 'error');
        } finally {
            setLoading(false);
        }
    }, [addToast, loading]);

    React.useEffect(() => {
        fetchBooks();
        const interval = setInterval(fetchBooks, 5000);
        return () => clearInterval(interval);
    }, [fetchBooks]);

    React.useEffect(() => {
        if (!selectedBook) { setBookDetail(null); return; }
        setDetailLoading(true);
        apiFetch(`/wolfmed/books/${selectedBook}`)
            .then(data => setBookDetail(data))
            .catch(err => addToast('Failed to load book detail: ' + err.message, 'error'))
            .finally(() => setDetailLoading(false));
    }, [selectedBook, addToast]);

    const chunkStatusColor = (status) => ({
        pending:   'var(--muted)',
        queued:    '#f5a623',
        extracted: '#4a9eff',
        validating:'#b47aff',
        validated: 'var(--done)',
        done:      'var(--done)',
        failed:    'var(--failed)',
    }[status] || 'var(--muted)');

    const mergeStatusColor = (status) => ({
        pending: 'var(--muted)',
        done:    'var(--done)',
        failed:  'var(--failed)',
        queued:  '#f5a623',
    }[status] || 'var(--muted)');

    if (loading) return <div style={{ padding: '2rem', color: 'var(--muted)' }}>Loading Wolfmed data...</div>;

    return (
        <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
            <div style={{ width: '320px', flexShrink: 0, borderRight: '1px solid var(--border)', overflow: 'auto', padding: '1rem' }}>
                {stats && (
                    <div style={{ marginBottom: '1.5rem' }}>
                        <div style={{ fontSize: '0.65em', letterSpacing: '0.12em', color: 'var(--muted)', textTransform: 'uppercase', fontFamily: 'var(--font-ui)', marginBottom: '0.75rem' }}>Pipeline Stats</div>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
                            {[
                                { label: 'Books',     value: stats.total_books },
                                { label: 'Merged',    value: stats.merged_books },
                                { label: 'Chunks',    value: stats.total_chunks },
                                { label: 'Validated', value: stats.validated_chunks },
                            ].map(({ label, value }) => (
                                <div key={label} style={{ background: 'var(--surface2)', borderRadius: '4px', padding: '0.5rem 0.75rem' }}>
                                    <div style={{ fontSize: '1.1em', fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>{value}</div>
                                    <div style={{ fontSize: '0.65em', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{label}</div>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
                <div style={{ fontSize: '0.65em', letterSpacing: '0.12em', color: 'var(--muted)', textTransform: 'uppercase', fontFamily: 'var(--font-ui)', marginBottom: '0.5rem' }}>Books</div>
                {books.length === 0 && <div style={{ color: 'var(--muted)', fontSize: '0.8em' }}>No books in pipeline yet.</div>}
                {books.map(book => {
                    const pct = book.total_chunks > 0 ? Math.round((book.validated_count / book.total_chunks) * 100) : 0;
                    const isSelected = selectedBook === book.book;
                    const trimmed = (book.skipped_front || 0) + (book.skipped_back || 0);
                    return (
                        <div key={book.book} onClick={() => setSelectedBook(isSelected ? null : book.book)}
                            style={{ padding: '0.75rem', marginBottom: '0.5rem', background: isSelected ? 'var(--surface2)' : 'transparent', border: isSelected ? '1px solid var(--border)' : '1px solid transparent', borderRadius: '4px', cursor: 'pointer' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.35rem' }}>
                                <span style={{ fontWeight: 600, fontSize: '0.85em', fontFamily: 'var(--font-ui)', color: 'var(--text)' }}>{book.category}</span>
                                <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
                                    {book.auto_trim && <span style={{ fontSize: '0.6em', color: '#4a9eff', fontFamily: 'var(--font-mono)', background: 'rgba(74,158,255,0.12)', padding: '1px 5px', borderRadius: '3px', letterSpacing: '0.06em' }}>AUTO</span>}
                                    <span style={{ fontSize: '0.7em', color: mergeStatusColor(book.merge_status), fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{book.merge_status}</span>
                                </div>
                            </div>
                            <div style={{ height: '4px', background: 'var(--border)', borderRadius: '2px', overflow: 'hidden', marginBottom: '0.3rem' }}>
                                <div style={{ height: '100%', width: `${pct}%`, background: book.merge_status === 'done' ? 'var(--done)' : 'var(--in-progress)', borderRadius: '2px', transition: 'width 0.5s' }} />
                            </div>
                            <div style={{ fontSize: '0.7em', color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>
                                {book.validated_count}/{book.total_chunks} validated ({pct}%)
                                {trimmed > 0 && <span style={{ marginLeft: '0.5rem', color: '#f5a623' }}>· {trimmed}pp skipped</span>}
                            </div>
                        </div>
                    );
                })}
            </div>
            <div style={{ flex: 1, overflow: 'auto', padding: '1rem' }}>
                {!selectedBook && <div style={{ color: 'var(--muted)', fontSize: '0.85em', marginTop: '2rem', textAlign: 'center' }}>Select a book to view chunk details</div>}
                {selectedBook && detailLoading && <div style={{ color: 'var(--muted)', fontSize: '0.85em' }}>Loading...</div>}
                {selectedBook && bookDetail && !detailLoading && (
                    <>
                        <div style={{ marginBottom: '1rem' }}>
                            <div style={{ fontSize: '1em', fontWeight: 700, fontFamily: 'var(--font-ui)', color: 'var(--text)', marginBottom: '0.25rem' }}>{bookDetail.category}</div>
                            <div style={{ fontSize: '0.75em', color: 'var(--muted)', fontFamily: 'var(--font-mono)', marginBottom: '0.4rem' }}>
                                Mode: {bookDetail.mode} · Run: {bookDetail.run_id ? bookDetail.run_id.slice(0,8) : 'n/a'} · {bookDetail.created_at ? bookDetail.created_at.slice(0,16) : ''}
                            </div>
                            {bookDetail.total_pages != null && (
                                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', fontSize: '0.7em', fontFamily: 'var(--font-mono)' }}>
                                    <span style={{ background: 'var(--surface2)', padding: '2px 7px', borderRadius: '3px', color: 'var(--text)' }}>
                                        {bookDetail.content_pages ?? bookDetail.total_pages}
                                        <span style={{ color: 'var(--muted)' }}>/{bookDetail.total_pages} pages</span>
                                    </span>
                                    {bookDetail.skipped_front > 0 && (
                                        <span style={{ background: 'rgba(245,166,35,0.12)', padding: '2px 7px', borderRadius: '3px', color: '#f5a623' }}>
                                            ↓ {bookDetail.skipped_front}pp front
                                        </span>
                                    )}
                                    {bookDetail.skipped_back > 0 && (
                                        <span style={{ background: 'rgba(245,166,35,0.12)', padding: '2px 7px', borderRadius: '3px', color: '#f5a623' }}>
                                            ↑ {bookDetail.skipped_back}pp back
                                        </span>
                                    )}
                                    {bookDetail.auto_trim && (
                                        <span style={{ background: 'rgba(74,158,255,0.12)', padding: '2px 7px', borderRadius: '3px', color: '#4a9eff' }}>
                                            auto-trim
                                        </span>
                                    )}
                                    {bookDetail.content_start_page != null && (
                                        <span style={{ color: 'var(--muted)', padding: '2px 4px' }}>
                                            content pp {bookDetail.content_start_page}–{bookDetail.content_end_page}
                                        </span>
                                    )}
                                </div>
                            )}
                        </div>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-mono)', fontSize: '0.75em' }}>
                            <thead>
                                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                                    <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', color: 'var(--muted)', fontWeight: 600 }}>Chunk</th>
                                    <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', color: 'var(--muted)', fontWeight: 600 }}>Pages</th>
                                    <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', color: 'var(--muted)', fontWeight: 600 }}>Status</th>
                                    <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', color: 'var(--muted)', fontWeight: 600 }}>Extract Task</th>
                                    <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', color: 'var(--muted)', fontWeight: 600 }}>Validate Task</th>
                                </tr>
                            </thead>
                            <tbody>
                                {(bookDetail.chunks || []).map((chunk, i) => (
                                    <tr key={chunk.id} style={{ borderBottom: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                                        <td style={{ padding: '0.4rem 0.5rem', color: 'var(--text)' }}>{chunk.id}</td>
                                        <td style={{ padding: '0.4rem 0.5rem', color: 'var(--muted)' }}>{chunk.pages}</td>
                                        <td style={{ padding: '0.4rem 0.5rem' }}><span style={{ color: chunkStatusColor(chunk.status), fontWeight: 600 }}>{chunk.status}</span></td>
                                        <td style={{ padding: '0.4rem 0.5rem', color: 'var(--muted)', maxWidth: '160px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{chunk.extract_task_id || '—'}</td>
                                        <td style={{ padding: '0.4rem 0.5rem', color: 'var(--muted)', maxWidth: '160px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{chunk.validate_task_id || '—'}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </>
                )}
            </div>
        </div>
    );
};

const App = () => {
    const [tasks, setTasks] = useState([]);
    const [selectedTaskId, setSelectedTaskId] = useState(null);
    const [selectedTaskDetail, setSelectedTaskDetail] = useState(null);
    const [daemonStatus, setDaemonStatus] = useState({ running: false, pid: null });
    const [toasts, setToasts] = useState([]);
    const [activeTab, setActiveTab] = useState('tasks');
    const wsRef = useRef(null);
    const wsReconnectTimeoutRef = useRef(null);

    const addToast = useCallback((message, type = 'info') => {
        const id = toastIdCounter++;
        setToasts(prev => [...prev, { id, message, type }]);
        setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 3000);
    }, []);

    const removeToast = useCallback((id) => setToasts(prev => prev.filter(t => t.id !== id)), []);

    const fetchInitialData = useCallback(async () => {
        try {
            const [tasksData, daemonData] = await Promise.all([apiFetch('/tasks'), apiFetch('/daemon/status')]);
            setTasks(tasksData); setDaemonStatus(daemonData);
        } catch (error) { addToast(`Failed to load initial data: ${error.message}`, 'error'); }
    }, [addToast]);

    const connectWebSocket = useCallback(() => {
        if (wsReconnectTimeoutRef.current) { clearTimeout(wsReconnectTimeoutRef.current); wsReconnectTimeoutRef.current = null; }
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) wsRef.current.close();
        wsRef.current = new WebSocket(WS_URL);
        wsRef.current.onopen = () => addToast('Connected to real-time updates.', 'success');
        wsRef.current.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (Array.isArray(data)) setTasks(data);
            else if (data.daemonStatus) setDaemonStatus(data.daemonStatus);
        };
        wsRef.current.onclose = () => { addToast('WebSocket disconnected. Reconnecting in 5s...', 'warning'); wsReconnectTimeoutRef.current = setTimeout(connectWebSocket, 5000); };
        wsRef.current.onerror = () => { if (wsRef.current && wsRef.current.readyState !== WebSocket.CLOSED) wsRef.current.close(); };
    }, [addToast]);

    useEffect(() => {
        fetchInitialData(); connectWebSocket();
        return () => { if (wsRef.current) wsRef.current.close(); if (wsReconnectTimeoutRef.current) clearTimeout(wsReconnectTimeoutRef.current); };
    }, [fetchInitialData, connectWebSocket]);

    useEffect(() => {
        if (!selectedTaskId) { setSelectedTaskDetail(null); return; }
        apiFetch(`/tasks/${selectedTaskId}`).then(setSelectedTaskDetail).catch(err => { setSelectedTaskDetail(null); addToast(`Failed to load task detail: ${err.message}`, 'error'); });
    }, [selectedTaskId, addToast]);

    const handleTaskSelect   = useCallback((id) => setSelectedTaskId(id), []);
    const handleTaskDelete   = useCallback(async (id) => {
        if (window.confirm(`Delete task ${id.substring(0,8)}?`)) {
            try {
                await apiFetch(`/tasks/${id}`, 'DELETE');
                setTasks(prev => prev.filter(t => t.id !== id));
                if (selectedTaskId === id) { setSelectedTaskId(null); setSelectedTaskDetail(null); }
                addToast(`Task ${id.substring(0,8)} deleted.`, 'success');
            } catch (error) { addToast(`Failed to delete task: ${error.message}`, 'error'); }
        }
    }, [selectedTaskId, addToast]);

    const handleDaemonRestart = useCallback(async () => {
        try {
            setDaemonStatus(prev => ({ ...prev, running: false, pid: null }));
            const result = await apiFetch('/daemon/restart', 'POST');
            setDaemonStatus({ running: true, pid: result.pid });
            addToast('Daemon restarted.', 'success');
        } catch (error) { addToast(`Failed to restart daemon: ${error.message}`, 'error'); }
    }, [addToast]);

    return (
        <>
            <TopBar tasks={tasks} daemonStatus={daemonStatus} onRestartDaemon={handleDaemonRestart} />
            <div className="tab-nav">
                <button className={activeTab==='tasks'     ?'active':''} onClick={() => setActiveTab('tasks')}>Tasks</button>
                <button className={activeTab==='submit'    ?'active':''} onClick={() => setActiveTab('submit')}>Submit Task</button>
                <button className={activeTab==='designs'   ?'active':''} onClick={() => setActiveTab('designs')}>Designs</button>
                <button className={activeTab==='reviews'   ?'active':''} onClick={() => setActiveTab('reviews')}>Q1 Reviews</button>
                <button className={activeTab==='pipelines' ?'active':''} onClick={() => setActiveTab('pipelines')}>Pipelines</button>
                <button className={activeTab==='rc'        ?'active':''} onClick={() => setActiveTab('rc')}>Research Center</button>
                <button className={activeTab==='axellog'   ?'active':''} onClick={() => setActiveTab('axellog')}>Axel Log</button>
                <button className={activeTab==='wolfmed'   ?'active':''} onClick={() => setActiveTab('wolfmed')}>Wolfmed</button>
                <button className={activeTab==='help'      ?'active':''} onClick={() => setActiveTab('help')}>Help</button>
            </div>
            {activeTab === 'tasks' && (
                <div style={{ display:'grid', gridTemplateColumns:'8fr 4fr', flexGrow:1, overflow:'hidden', minHeight:0 }}>
                    <div style={{ display:'flex', flexDirection:'column', overflow:'hidden' }}>
                        <DaemonHealthPanel daemonStatus={daemonStatus} tasks={tasks} addToast={addToast} />
                        <TaskQueueTable tasks={tasks} selectedTaskId={selectedTaskId} onSelectTask={handleTaskSelect} />
                    </div>
                    <TaskLogPanel detail={selectedTaskDetail} onDeleteTask={handleTaskDelete} />
                </div>
            )}
            {activeTab === 'submit'    && <div className="main-content"><TaskSubmitPanel addToast={addToast} /></div>}
            {activeTab === 'designs'   && <div className="main-content"><DesignsPanel addToast={addToast} /></div>}
            {activeTab === 'reviews'   && <div className="main-content"><ReviewsPanel /></div>}
            {activeTab === 'pipelines' && <div className="main-content"><PipelineLauncherPanel addToast={addToast} /></div>}
            {activeTab === 'rc'        && <div className="main-content"><ResearchCenterPanel addToast={addToast} /></div>}
            {activeTab === 'axellog'   && <div className="main-content" style={{ display:'flex', flexDirection:'column', padding:0 }}><AxelLogPanel /></div>}
            {activeTab === 'wolfmed'   && <div className="main-content" style={{ padding: 0 }}><WolfmedPanel addToast={addToast} /></div>}
            {activeTab === 'help'      && <div className="main-content"><HelpPanel /></div>}
            <Toast toasts={toasts} onRemoveToast={removeToast} />
        </>
    );
};

const TASK_TYPES = ['research', 'write', 'code', 'design', 'frontend', 'review', 'test', 'assets', 'git', 'general'];

const TASK_TYPE_HINTS = {
    research:  'R1 — Gemini 3.1 Pro. Live web search. Use for: market research, competitive analysis, tech landscape.',
    write:     'W1 — Gemini 2.5 Flash. Use for: docs, content, markdown reports, summaries.',
    code:      'D1 — Ollama (local). Use for: Python scripts, CLI tools, backend logic.',
    design:    'UX1 — Gemini 3 Flash. Use for: HTML prototypes, UI concepts, design specs.',
    frontend:  'FE1 — Gemini 2.5 Flash. Use for: React components, Next.js pages, implementation plans.',
    review:    'Q1 — Gemini 3.1 Pro. Use for: design critique, code review, quality assessment.',
    test:      'T1 — Gemini 2.5 Flash. Use for: Playwright tests, test plans, QA specs.',
    assets:    'A1 — Nano Banana Pro. Use for: image generation, icons, visual assets.',
    git:       'G1 — Ollama (local). Prompt is OPTIONAL — daemon auto-reads git diff and injects it. Just submit with an empty prompt or add a short note. Never pushes.',
    general:   'Gemini 2.5 Flash. Use for: anything that doesn\'t fit the above.',
};

const TaskSubmitPanel = ({ addToast }) => {
    const [taskType, setTaskType] = useState('research');
    const [prompt, setPrompt]     = useState('');
    const [outputFile, setOutputFile] = useState('');
    const [loading, setLoading]   = useState(false);

    const handleSubmit = async () => {
        if (!prompt.trim() && taskType !== 'git') { addToast('Prompt is required.', 'error'); return; }
        setLoading(true);
        try {
            const data = await apiFetch('/tasks', 'POST', { type: taskType, prompt: prompt.trim(), output_file: outputFile.trim() });
            addToast(`Task queued — ${data.task_id}`, 'success');
            setPrompt('');
            setOutputFile('');
        } catch (err) {
            addToast(`Failed: ${err.message}`, 'error');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="panel-page">
            <div className="panel-form">
                <div className="panel-form-header">Submit Task</div>

                <div>
                    <label className="panel-label">Task Type</label>
                    <select className="panel-select" value={taskType} onChange={e => setTaskType(e.target.value)}>
                        {TASK_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                    </select>
                </div>

                <div className="panel-hint">{TASK_TYPE_HINTS[taskType]}</div>

                <div>
                    <label className="panel-label">Prompt</label>
                    <textarea
                        className="panel-textarea"
                        value={prompt}
                        onChange={e => setPrompt(e.target.value)}
                        placeholder="Describe what you want the agent to do..."
                        rows={7}
                        style={{ resize: 'vertical', lineHeight: 1.6 }}
                    />
                </div>

                <div>
                    <label className="panel-label">Output File <span style={{ fontWeight: 400, textTransform: 'none', opacity: 0.6 }}>(optional — relative to workspace root)</span></label>
                    <input
                        type="text"
                        className="panel-input"
                        value={outputFile}
                        onChange={e => setOutputFile(e.target.value)}
                        placeholder="e.g. WesaTeam/agent-ecosystem/logs/my-output.md"
                    />
                </div>

                <button className="btn-primary" onClick={handleSubmit} disabled={loading} style={{ alignSelf: 'flex-start' }}>
                    {loading ? 'Queuing...' : 'Queue Task →'}
                </button>
            </div>
        </div>
    );
};

const DOCS = [
    {
        section: 'Tasks Tab',
        items: [
            { title: 'Task Queue Table', body: 'Lists all tasks. Filterable by today / all-time. Sortable by status, model, or created date. Click any row to load details in the right panel.' },
            { title: 'Task Detail Panel', body: 'Three tabs: Overview (prompt + metadata), Result (agent output), Error (failure reason). Delete button removes the task from queue and DB.' },
            { title: 'RC Scheduler Toggle', body: 'ON/OFF switch for the autonomous Research Center scheduler. When ON, the daemon fires one research-center pipeline run every RC_INTERVAL_HOURS (default 4h), rotating through all 5 topics in round-robin. State persists in logs/rc_enabled.flag — survives restarts. Default: OFF.' },
            { title: 'Daemon Health Panel', body: 'Shows PID, task counts by status, and daemon running state. Daemon is the background process that executes tasks. Restart button sends SIGTERM and relaunches daemon.py.' },
        ],
    },
    {
        section: 'Submit Task Tab',
        items: [
            { title: 'What it does', body: 'Manually queue a single task for any agent. The daemon picks it up within 3 seconds.' },
            { title: 'Task Types & Agents', body: 'research → R1 (web search, Gemini 3.1 Pro) | write → W1 (Gemini 3 Flash) | code → D1 (Ollama qwen3.5:35b) | design → UX1 (Gemini 3 Flash) | frontend → FE1 (Gemini 3 Flash) | review → Q1 (Gemini 3.1 Pro) | test → T1 | assets → A1 (Nano Banana Pro) | git → G1 (Ollama qwen3.5:35b, local) | general → W1' },
            { title: 'git task — zero effort', body: 'Select type "git" and submit with an empty prompt (or a short note). The daemon auto-runs git status + git diff HEAD, injects the output into G1\'s context, and G1 returns a ready-to-run PowerShell commit block. No copy-pasting diffs.' },
            { title: 'Output File', body: 'Optional. Path relative to Coworker/ workspace root. If set, daemon writes agent result to that file on completion. E.g. WesaTeam/agent-ecosystem/logs/my-output.md' },
            { title: 'Routing rule', body: 'Routing is by task_type only — the agent field in pipeline YAMLs is documentation. task_type must be unique across all skill YAMLs or the last file alphabetically wins silently.' },
        ],
    },
    {
        section: 'Pipelines Tab',
        items: [
            { title: 'research-center', body: 'R1 researches a topic → W1 synthesizes a WESA task brief → brief staged for Axel review. Runs once per topic. 5 topics in rotation. Trigger: manual (or auto via RC Scheduler).' },
            { title: 'design-loop', body: 'R1 researches 3 ranked design directions → UX1 prototypes the most original → Q1 scores (target 72/80) → SU1 updates skill overlay → loops back to UX1. Max 8 iterations. On approval: SU1 permanently merges tips into ux_agent.yaml. Trigger: manual.' },
            { title: 'Pipeline Architecture', body: 'Stages defined in agents/pipelines/*.yaml. loop_to field creates the iteration loop. stop_if: "score >= 72" parses Q1 output for X/80 pattern. {stage.<id>} injects previous stage output. {read_file:<path>} injects file contents at queue time.' },
        ],
    },
    {
        section: 'Wolfmed Pipeline',
        items: [
            { title: 'Overview', body: 'Takes a Polish medical PDF → produces a validated JSON question bank. Four phases: Chunk → Generate (EX1) → Validate (EX1) → Merge. Spans two directories: extractor/ (source, chunks, output) and agent-ecosystem/ (daemon tasks, agent outputs).' },
            { title: 'Phase 1 — Chunk', body: 'Script: pdf_chunker.py. Drop PDF into extractor/intake/<category>/. Run wolfmed_intake.py --category <name>. Splits PDF into 10-page chunks, extracts text to .txt, writes chunks to extractor/chunks/<category>/, creates manifest at extractor/data/<book>_manifest.json, drops wm-ex-* task files into agent-ecosystem/tasks/.' },
            { title: 'Chunker — Static Trim (Option 1)', body: 'Skip known boilerplate pages unconditionally. --skip-start N skips first N pages (title, copyright, ToC, preface). --skip-end N skips last N pages (bibliography, index, blank). Example: python wolfmed_intake.py --category fizjologia --skip-start 12 --skip-end 5' },
            { title: 'Chunker — Auto-Trim (Option 2)', body: 'Heuristic boundary detection. --auto-trim scans up to 20 pages at each boundary (--auto-trim-window N to change). Skips pages that are: nearly blank (<150 chars), table of contents (dot-leader lines), dense short lines >72% (index/ToC), or contain Polish structural keywords (spis treści, bibliografia, piśmiennictwo, indeks, ©, isbn). Prints per-page decision to console. Example: python wolfmed_intake.py --category fizjologia --auto-trim' },
            { title: 'Chunker — Combined (recommended)', body: 'Static skip runs first, auto-trim refines within the remaining range. Example: python wolfmed_intake.py --category fizjologia --skip-start 5 --auto-trim. Use for unknown books where you want to hard-cut obvious front matter and let heuristics find the exact content boundary.' },
            { title: 'Phase 2 — Generate (EX1)', body: 'Daemon picks up wm-ex-* tasks automatically (3s poll). EX1 (gemini-3.1-pro-preview, MODE: GENERATE) reads chunk text and generates 30–80 Polish Q&A questions grounded in source text. Writes raw JSON to agent-ecosystem/extractor/outputs/<chunk>_raw.json.' },
            { title: 'Phase 3 — Validate (EX1)', body: 'Re-run wolfmed_intake.py when generate tasks are done. EX1 (MODE: VALIDATE) checks each question: grounded in source? correct answer in text? distractors similar length? exactly one isCorrect: true? Removes failing questions — no fixes, no regeneration. Writes validated JSON to agent-ecosystem/extractor/outputs/<chunk>_validated.json.' },
            { title: 'Phase 4 — Merge (Python only)', body: 'Fires automatically when validated_count == total_chunks. merge_utils.py loads all *_validated.json, normalises schema, deduplicates by question text, checks UUID uniqueness against extractor/data/wolfmed.db (SQLite), writes final file to extractor/done/<category>_all_merged.json.' },
            { title: 'UUID Rules', body: 'All question IDs must be valid UUID v4: hex-only characters 0-9 and a-f, format xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx. Letters g–z are forbidden. EX1 agent.yaml enforces this. If bad UUIDs are found, run: python extractor/scripts/fix_uuids.py <merged_json> to repair both the JSON and wolfmed.db.' },
            { title: 'How to Run — Full', body: '1. Copy PDF to extractor/intake/<category>/   2. python wolfmed_intake.py --category <name> [--skip-start N] [--skip-end N] [--auto-trim]   3. Wait for generate tasks (watch Wolfmed tab or AXEL_BRIEFING.md)   4. Re-run python wolfmed_intake.py --category <name> → queues validate tasks   5. Re-run again when validate done → merge fires automatically.' },
            { title: 'How to Run — Resume', body: 'Intake is idempotent. Re-running the same command is always safe: existing manifest → skips re-chunking, done tasks → advances pipeline state, all validated → triggers merge. Just run: python wolfmed_intake.py --category <name>' },
            { title: 'Wolfmed Tab', body: 'Shows pipeline stats (total books, merged, chunks, validated), per-book progress bar, trim info (pages skipped front/back, auto-trim badge), and per-chunk status table (pending → queued → extracted → validating → validated).' },
            { title: 'Key Rules', body: 'One category per run — never mix categories. Do not delete manifest mid-run — it is the pipeline state. Do not run --watch without --category. Merge output is append-safe — UUID registry prevents duplicates across runs. Agent outputs go to agent-ecosystem/extractor/outputs/ (relative to WORKSPACE_ROOT).' },
        ],
    },
    {
        section: 'Designs Tab',
        items: [
            { title: 'Concept Preview', body: 'Lists all HTML prototype files from static/designs/. Click to preview in iframe. Concepts are outputs from UX1 via the design-loop pipeline.' },
            { title: 'Approve', body: 'Queues three tasks: UX1 polish pass, FE1 implementation plan, Q1 final review. This is the human gate in the design loop — only approved designs trigger skill_merge.' },
            { title: 'Retry', body: 'Add feedback, dismiss the current concept, and queue a fresh UX1 design task. Feedback is injected into the UX1 prompt.' },
            { title: 'File naming', body: 'concept-* = raw prototype | approved-* = approved for polish | polished-* = finished. Prefixes control which actions are shown.' },
        ],
    },
    {
        section: 'Q1 Reviews Tab',
        items: [
            { title: 'What it shows', body: 'All Q1 review markdown files from logs/designs/. Each review scores 8 dimensions (Visual Hierarchy, Color, Typography, Layout, Consistency, Craft, Originality, Emotional Register) out of 10 each. Total 80.' },
            { title: 'Stop condition', body: 'Design loop exits when Q1 total score >= 72/80. On exit: skill_merge stage fires, SU1 merges accumulated overlay tips into ux_agent.yaml permanently.' },
        ],
    },
    {
        section: 'Agent Roster',
        items: [
            { title: 'Axel', body: 'Team Lead. Claude Sonnet via Cowork. Orchestrates all other agents. Greg only talks to Axel.' },
            { title: 'R1', body: 'Researcher. gemini-3.1-pro-preview. use_web_search: true. Live Google Search grounding. 120s timeout (concurrent.futures wrapper).' },
            { title: 'W1', body: 'Writer. gemini-3-flash-preview. Docs, content, markdown, PRDs, synthesis.' },
            { title: 'D1', body: 'Developer. qwen3.5:35b (Ollama, local). Python, backend, CLI. Keep tasks to ONE file — hallucinates on multi-file context.' },
            { title: 'UX1', body: 'Designer. gemini-3-flash-preview. HTML prototypes. Two-tier skill system: per-run overlay during loop, permanent merge to ux_agent.yaml on approval only.' },
            { title: 'FE1', body: 'Frontend Engineer. gemini-3-flash-preview. React 19, Next.js, implementation plans.' },
            { title: 'Q1', body: 'Design Critic. gemini-3.1-pro-preview. Independent voice — framed as external critic, not team member. Scores designs 0–80.' },
            { title: 'SU1', body: 'Skill Updater. gemini-2.5-flash. Reads Q1 reviews, writes per-run overlays. Merges to ux_agent.yaml on pipeline approval. Requires full current skill file in prompt to avoid truncation.' },
            { title: 'PM1', body: 'Project Manager. gemini-3-flash-preview. Coordinates FE1+T1 build-test loops. Not yet in production (Sprint 2).' },
            { title: 'T1', body: 'Tester. gemini-2.5-flash. Playwright, test plans, QA specs.' },
            { title: 'A1', body: 'Assets. gemini-2.5-flash + Nano Banana Pro API. Image generation, icons. API key valid till May 2026.' },
            { title: 'G1', body: 'Git Agent. qwen3.5:35b (Ollama, local). Prompt is optional — daemon auto-runs git status + git diff HEAD and injects the output before routing. G1 reads the actual diff and produces a PowerShell-ready git add + commit block. Never pushes. Greg reviews and runs the block manually.' },
        ],
    },
    {
        section: 'Infrastructure',
        items: [
            { title: 'Daemon', body: 'daemon.py — polls tasks/ folder every 3s, imports JSON to SQLite, routes by task_type, executes via Router → GeminiAgent or OllamaAgent. No restart needed for new tasks — drop JSON and daemon picks up.' },
            { title: 'Task Queue', body: 'SQLite-backed (logs/tasks.db). JSON files in tasks/ are imported on every poll via sync_json_to_db(). Direct SQLite writes unreliable on Windows NTFS mount — always use JSON file drop.' },
            { title: 'Gemini Timeout', body: 'All Gemini calls wrapped in concurrent.futures with 120s hard timeout (GEMINI_TIMEOUT env var). Prevents tasks hanging forever on API stall.' },
            { title: 'Notifications (ntfy)', body: 'Set NTFY_TOPIC env var. Daemon pushes: task done, task failed, daemon started, pipeline complete. See the ntfy / Remote Control section for inbound commands.' },
            { title: 'Git rules', body: 'agent-ecosystem/ → GitHub (public, safe to push). All WesaTeam/ folders → LOCAL ONLY, confidential, never push. Axel stages files, Greg commits from Windows PowerShell.' },
            { title: 'output_file paths', body: 'Resolved relative to WORKSPACE_ROOT (Coworker/). Always prefix: WesaTeam/agent-ecosystem/<path> for ecosystem files. Never bare paths — they write to Coworker root.' },
        ],
    },
    {
        section: 'ntfy / Remote Control',
        items: [
            { title: 'Setup', body: 'Set NTFY_TOPIC=your-private-topic in .env before starting the daemon. Subscribe on your phone via the ntfy app (iOS / Android) or https://ntfy.sh/your-topic. If NTFY_TOPIC is not set, all ntfy features are silently disabled.' },
            { title: 'Outbound — what the daemon pushes', body: '"WESA Daemon Started" on startup. "Task Done: <id>" with type + model on completion. "Task Failed: <id>" with error on failure. "Pipeline: <name>" with run ID + status on pipeline events.' },
            { title: 'status', body: 'Send "status" to get a 3-line reply: queue counts (Done/Failed/Pending), current sprint goal (truncated to 40 chars), design loop iteration + Q1 score if active.' },
            { title: 'pause / stop', body: 'Send "pause" — daemon finishes the current task, then holds until you delete logs/daemon_pause.flag (or restart). Send "stop" — daemon exits cleanly after the current task (same as Ctrl+C). Both are flag-file based and survive daemon restarts.' },
            { title: 'help', body: 'Send "help" — daemon replies with the full command list.' },
            { title: 'approve / retry', body: 'Send "approve <run_id>" or "retry <run_id>" — writes logs/approvals/<run_id>.approve (or .retry). Consumed by the design-loop pipeline as the human gate.' },
            { title: 'Submitting tasks remotely', body: 'Any message matching "type:prompt" is queued as a task. Examples: "D1:add rate limiting to notifier.py" → code task to D1. "R1:competitor pricing for AI agent platforms" → research task to R1. "W1:draft changelog for v0.8" → write task to W1. A message with no colon is queued as a "general" task.' },
            { title: 'Echo filter', body: 'The daemon ignores inbound messages containing "Model:", "Task Done:", or "Task Failed:" — these are its own outbound notifications echoed back by ntfy when subscribed to the same topic.' },
        ],
    },
];

const HelpPanel = () => {
    const [activeSection, setActiveSection] = React.useState(0);

    return (
        <div className="help-layout">
            <div className="help-sidebar">
                {DOCS.map((sec, si) => (
                    <div key={si} className={`help-sidebar-item ${activeSection === si ? 'active' : ''}`} onClick={() => setActiveSection(si)}>
                        {sec.section}
                    </div>
                ))}
            </div>
            <div className="help-content">
                {DOCS[activeSection] && DOCS[activeSection].items.map((item, ii) => (
                    <div key={ii}>
                        <div className="help-item-title">{item.title}</div>
                        <div className="help-item-body">{item.body}</div>
                    </div>
                ))}
            </div>
        </div>
    );
};

const PIPELINES = ['research-center', 'design-loop'];

const PipelineLauncherPanel = ({ addToast }) => {
    const [pipeline, setPipeline] = useState('research-center');
    const [topic, setTopic]       = useState('');
    const [loading, setLoading]   = useState(false);
    const [runs, setRuns]         = React.useState([]);
    const [selectedRun, setSelectedRun] = React.useState(null);

    React.useEffect(() => {
        const fetchRuns = () => apiFetch('/pipelines/running').then(setRuns).catch(() => {});
        fetchRuns();
        const i = setInterval(fetchRuns, 5000);
        return () => clearInterval(i);
    }, []);

    const handleLaunch = async () => {
        if (!topic.trim()) { addToast('Topic is required.', 'error'); return; }
        setLoading(true);
        try {
            const data = await apiFetch('/pipeline/start', 'POST', { pipeline, topic: topic.trim() });
            addToast(`Pipeline queued — task: ${data.task_id}`, 'success');
            setTopic('');
        } catch (err) {
            addToast(`Failed: ${err.message}`, 'error');
        } finally {
            setLoading(false);
        }
    };

    const runStatusColor = (s) => ({ running: 'var(--in-progress)', done: 'var(--done)', failed: 'var(--failed)' })[s] || 'var(--muted)';

    return (
        <div className="pipeline-layout">
            <div className="pipeline-list">
                <div style={{ padding: '8px 14px', fontSize: '0.68rem', fontWeight: 700, color: 'var(--muted)', borderBottom: '1px solid var(--border)', textTransform: 'uppercase', letterSpacing: '0.1em', background: 'var(--surface2)', fontFamily: 'var(--font-ui)' }}>
                    Pipeline Runs ({runs.length})
                </div>
                {runs.length === 0 && <div style={{ padding: '16px 14px', fontSize: '0.78rem', color: 'var(--muted)', fontFamily: 'var(--font-mono)', letterSpacing: '0.04em' }}>No runs yet.</div>}
                {runs.map(r => (
                    <div key={r.run_id} className={`pipeline-list-item ${selectedRun && selectedRun.run_id === r.run_id ? 'active' : ''}`} onClick={() => setSelectedRun(selectedRun && selectedRun.run_id === r.run_id ? null : r)}>
                        <span className="pipeline-name">{r.pipeline}</span>
                        <span className="pipeline-status" style={{ color: runStatusColor(r.status) }}>
                            {r.status}{r.iteration ? ` · iter ${r.iteration}` : ''}{r.topic ? ` · ${r.topic.substring(0, 28)}${r.topic.length > 28 ? '…' : ''}` : ''}
                        </span>
                        {selectedRun && selectedRun.run_id === r.run_id && r.stop_reason && (
                            <span className="pipeline-status">{r.stop_reason}</span>
                        )}
                    </div>
                ))}
            </div>
            <div className="panel-page" style={{ flex: 1 }}>
                <div className="panel-form">
                    <div className="panel-form-header">Launch Pipeline</div>
                    <div>
                        <label className="panel-label">Pipeline</label>
                        <select className="panel-select" value={pipeline} onChange={e => setPipeline(e.target.value)}>
                            {PIPELINES.map(p => <option key={p} value={p}>{p}</option>)}
                        </select>
                    </div>
                    <div>
                        <label className="panel-label">Topic</label>
                        <input
                            type="text"
                            className="panel-input"
                            value={topic}
                            onChange={e => setTopic(e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && handleLaunch()}
                            placeholder="e.g. AgentHire portal design patterns 2026"
                        />
                    </div>
                    <button className="btn-primary" onClick={handleLaunch} disabled={loading} style={{ alignSelf: 'flex-start' }}>
                        {loading ? 'Queuing...' : 'Start Pipeline'}
                    </button>
                </div>
            </div>
        </div>
    );
};

const AxelLogPanel = () => {
    const [tab, setTab] = React.useState('trigger');
    const [triggerLines, setTriggerLines] = React.useState([]);
    const [activityLines, setActivityLines] = React.useState([]);
    const [triggerTotal, setTriggerTotal] = React.useState(0);
    const [activityTotal, setActivityTotal] = React.useState(0);
    const triggerRef = React.useRef(null);
    const activityRef = React.useRef(null);

    const fetchLogs = React.useCallback(() => {
        apiFetch('/logs/axel').then(d => {
            if (d.lines) { setTriggerLines(d.lines); setTriggerTotal(d.total || 0); }
        }).catch(() => {});
        apiFetch('/logs/axel-activity').then(d => {
            if (d.lines) { setActivityLines(d.lines); setActivityTotal(d.total || 0); }
        }).catch(() => {});
    }, []);

    React.useEffect(() => {
        fetchLogs();
        const i = setInterval(fetchLogs, 5000);
        return () => clearInterval(i);
    }, [fetchLogs]);

    React.useEffect(() => {
        if (triggerRef.current) triggerRef.current.scrollTop = triggerRef.current.scrollHeight;
    }, [triggerLines]);
    React.useEffect(() => {
        if (activityRef.current) activityRef.current.scrollTop = activityRef.current.scrollHeight;
    }, [activityLines]);

    const tabBtn = (name, label, count) => (
        <button onClick={() => setTab(name)} style={{ background: 'none', border: 'none', borderBottom: tab === name ? '2px solid var(--done)' : '2px solid transparent', color: tab === name ? 'var(--done)' : 'var(--muted)', padding: '9px 16px', fontSize: '0.72em', fontWeight: 700, letterSpacing: '0.1em', cursor: 'pointer', textTransform: 'uppercase', fontFamily: 'var(--font-ui)', marginBottom: '-1px', borderRadius: 0, transition: 'color 0.15s, border-color 0.15s' }}>
            {label} <span style={{ opacity: 0.5, fontWeight: 400 }}>({count})</span>
        </button>
    );

    const lines = tab === 'trigger' ? triggerLines : activityLines;
    const total = tab === 'trigger' ? triggerTotal : activityTotal;
    const ref   = tab === 'trigger' ? triggerRef   : activityRef;

    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: 'var(--surface)' }}>
            <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', background: 'var(--surface2)', alignItems: 'center', padding: '0 8px', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex' }}>
                    {tabBtn('trigger',  'Session Log',  triggerLines.length)}
                    {tabBtn('activity', 'Activity Log', activityLines.length)}
                </div>
                <div style={{ fontSize: '0.68em', color: 'var(--muted)', fontFamily: 'var(--font-ui)', paddingRight: 8 }}>
                    showing last {lines.length} of {total} lines &nbsp;
                    <button onClick={fetchLogs} style={{ background: 'none', border: '1px solid var(--border)', color: 'var(--muted)', cursor: 'pointer', padding: '2px 8px', borderRadius: 3, fontSize: '0.9em', fontFamily: 'var(--font-ui)' }}>Refresh</button>
                </div>
            </div>
            <div ref={ref} style={{ flex: 1, overflowY: 'auto', fontFamily: 'var(--font-mono)', fontSize: '0.75em', lineHeight: 1.6, color: 'var(--text)', background: 'var(--surface)', padding: '12px 16px', whiteSpace: 'pre-wrap', wordBreak: 'break-all', borderTop: '1px solid var(--border)' }}>
                {lines.length === 0
                    ? <span style={{ color: 'var(--muted)' }}>No log entries yet. Trigger an axel task via ntfy to see output here.</span>
                    : lines.map((l, i) => <div key={i}>{l}</div>)
                }
            </div>
        </div>
    );
};

createRoot(document.getElementById('root')).render(<App />);