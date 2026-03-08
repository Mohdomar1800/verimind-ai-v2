import React, { useState, useEffect } from 'react';
import { getDebugPriorities, getExplanation } from '../services/api';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts';

const SEVERITY_COLORS = {
  3: '#FB5E4C',  // Critical
  2: '#F7CC66',  // High
  1: '#98E4C1',  // Medium
  0: '#9AD7FD',  // Low
};

const SEVERITY_LABELS = {
  3: 'Critical',
  2: 'High',
  1: 'Medium',
  0: 'Low',
};

function DebugQueue({ runId, featureRunId, onBack }) {
  const [debugItems, setDebugItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedItem, setSelectedItem] = useState(null);
  const [explanation, setExplanation] = useState(null);
  const [filter, setFilter] = useState('all');
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchData();
  }, [runId, featureRunId]);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getDebugPriorities(runId, featureRunId);
      const mapped = (data.items || []).map(item => ({
        ...item,
        priority_score: item.score * 100, // Scale to 0-100 like the UI expects
        severity_num: item.evidence?.severity_num || 0,
        similarity_avg: item.evidence?.similarity_avg || 0.5,
        module_name: item.evidence?.module_name || (item.item_id.includes(':') ? item.item_id.split(':')[1] : item.item_id),
        repo: item.evidence?.repo || (item.item_id.includes(':') ? item.item_id.split(':')[0] : 'unknown'),
        message: item.evidence?.message || 'Failure signature detected',
        context_score: item.evidence?.context_score || 0, // Extract context_score
        failure_id: item.evidence?.failure_id || item.item_id, // Extract failure_id
      }));
      setDebugItems(mapped);
    } catch (error) {
      console.error('Failed to fetch debug priorities:', error);
      setError('Backend is down or data unavailable.');
    }
    setLoading(false);
  };



  const handleRowClick = async (item) => {
    setSelectedItem(item);
    try {
      const exp = await getExplanation(item.item_id || item.failure_id);
      setExplanation(exp);
    } catch (error) {
      console.error('Failed to fetch explanation:', error);
      setExplanation({
        reasons: [
          `Failed to fetch real explanation from backend.`,
        ],
        evidence: {
          error: "Backend is down or data unavailable"
        },
      });
    }
  };

  const filteredItems = filter === 'all'
    ? debugItems
    : debugItems.filter(i => i.severity_num === parseInt(filter));

  const chartData = filteredItems.slice(0, 8).map(d => ({
    name: d.failure_id?.slice(0, 10) || (d.module_name || '').slice(0, 10),
    score: d.priority_score,
    severity: d.severity_num,
  }));

  // Severity distribution
  const severityDist = debugItems.reduce((acc, d) => {
    const sev = d.severity_num || 0;
    acc[sev] = (acc[sev] || 0) + 1;
    return acc;
  }, {});

  if (loading) {
    return (
      <div className="main-container">
        <div className="loading">Building debug queue...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="main-container">
        <div className="page-header">
          <h1 className="page-title">Debug Queue</h1>
          <button className="back-btn" onClick={onBack}>← Go Back</button>
        </div>
        <div style={{ padding: '20px', background: '#ffebee', color: '#c62828', borderRadius: '8px', border: '1px solid #ef5350', marginTop: '20px' }}>
          <h3>System Error</h3>
          <p>{error}</p>
          <button className="btn btn-primary" style={{ marginTop: '10px' }} onClick={fetchData}>Retry Data Fetch</button>
        </div>
      </div>
    );
  }

  return (
    <div className="main-container">
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 className="page-title">Debug Prioritization Queue</h1>
          <p style={{ color: '#444', fontSize: '13px', marginTop: '-5px' }}>
            Context: <span style={{ fontWeight: '600' }}>{runId}</span>
          </p>
        </div>
        <div style={{ display: 'flex', gap: '10px' }}>
          <button className="btn btn-secondary" onClick={fetchData}>Refresh Queue</button>
          <button className="back-btn" onClick={onBack}>← Go Back</button>
        </div>
      </div>

      <p style={{ marginBottom: '20px', fontSize: '16px' }}>
        AI-based debug prioritization to help you fix the most critical issues first, using severity, similarity, and context signals.
      </p>

      {/* Stats */}
      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-value">{severityDist[3] || 0}</div>
          <div className="stat-label">Critical</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{severityDist[2] || 0}</div>
          <div className="stat-label">High</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{severityDist[1] || 0}</div>
          <div className="stat-label">Medium</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{severityDist[0] || 0}</div>
          <div className="stat-label">Low</div>
        </div>
      </div>

      {/* Filter */}
      <div style={{ marginBottom: '20px', display: 'flex', gap: '10px', alignItems: 'center' }}>
        <span style={{ fontWeight: 600 }}>Filter by Severity:</span>
        <button
          className={`btn ${filter === 'all' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setFilter('all')}
          style={{ padding: '8px 16px' }}
        >
          All
        </button>
        <button
          className={`btn ${filter === '3' ? 'btn-danger' : 'btn-secondary'}`}
          onClick={() => setFilter('3')}
          style={{ padding: '8px 16px' }}
        >
          Critical
        </button>
        <button
          className={`btn ${filter === '2' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setFilter('2')}
          style={{ padding: '8px 16px' }}
        >
          High
        </button>
        <button
          className={`btn ${filter === '1' ? 'btn-secondary' : 'btn-secondary'}`}
          onClick={() => setFilter('1')}
          style={{ padding: '8px 16px' }}
        >
          Medium
        </button>
      </div>

      <div className="two-columns">
        {/* Chart */}
        <div className="chart-container">
          <h3 className="chart-title">Priority Scores</h3>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={chartData} layout="vertical">
              <XAxis type="number" domain={[0, 100]} />
              <YAxis dataKey="name" type="category" width={80} tick={{ fontSize: 11 }} />
              <Tooltip />
              <Bar dataKey="score" stroke="black" strokeWidth={1}>
                {chartData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={SEVERITY_COLORS[entry.severity] || '#9AD7FD'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Explanation Panel */}
        <div className="chart-container">
          <h3 className="chart-title">Debug Evidence</h3>
          {selectedItem ? (
            <div className="evidence-panel">
              <div style={{ marginBottom: '10px', padding: '10px', background: SEVERITY_COLORS[selectedItem.severity_num], borderRadius: '5px' }}>
                <strong>{SEVERITY_LABELS[selectedItem.severity_num]} Severity</strong>
              </div>
              <h4 style={{ marginBottom: '10px' }}>
                {selectedItem.module_name} ({selectedItem.repo})
              </h4>
              <div style={{
                padding: '10px',
                background: '#333',
                color: '#0f0',
                fontFamily: 'monospace',
                borderRadius: '5px',
                marginBottom: '15px',
                fontSize: '13px'
              }}>
                {selectedItem.message || 'No message available'}
              </div>
              {explanation && (
                <>
                  <strong>Why debug this first?</strong>
                  <ul style={{ marginTop: '10px', paddingLeft: '20px' }}>
                    {explanation.reasons?.map((r, i) => (
                      <li key={i} style={{ marginBottom: '5px' }}>{r}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          ) : (
            <div className="empty-state">Click on a failure to see debug evidence</div>
          )}
        </div>
      </div>

      {/* Debug Queue Table */}
      <div className="data-table-container" style={{ marginTop: '20px' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Priority</th>
              <th>Failure ID</th>
              <th>Module</th>
              <th>Repository</th>
              <th>Severity</th>
              <th>Similarity</th>
              <th>Score</th>
            </tr>
          </thead>
          <tbody>
            {filteredItems.map((item, idx) => (
              <tr
                key={idx}
                onClick={() => handleRowClick(item)}
                style={{
                  cursor: 'pointer',
                  background: selectedItem?.item_id === item.item_id ? '#FEE3D2' : '',
                  borderLeft: `4px solid ${SEVERITY_COLORS[item.severity_num] || '#9AD7FD'}`
                }}
              >
                <td><strong>#{idx + 1}</strong></td>
                <td style={{ fontFamily: 'monospace' }}>{item.failure_id || item.item_id}</td>
                <td>{item.module_name}</td>
                <td>{item.repo}</td>
                <td>
                  <span style={{
                    padding: '4px 10px',
                    borderRadius: '15px',
                    background: SEVERITY_COLORS[item.severity_num],
                    fontSize: '12px',
                    fontWeight: 600
                  }}>
                    {SEVERITY_LABELS[item.severity_num] || 'Unknown'}
                  </span>
                </td>
                <td>{(item.similarity_avg || 0).toFixed(2)}</td>
                <td>
                  <strong style={{ color: (item.priority_score || 0) >= 70 ? '#FB5E4C' : 'black' }}>
                    {(item.priority_score || 0).toFixed(1)}
                  </strong>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Export */}
      <div style={{ marginTop: '20px', textAlign: 'right' }}>
        <button
          className="btn btn-secondary"
          onClick={() => {
            const csv = filteredItems.map((d, i) =>
              `${i + 1},${d.failure_id || d.item_id},${d.module_name},${d.repo},${d.severity_num},${d.similarity_avg || 0},${d.priority_score}`
            ).join('\n');
            const blob = new Blob([`rank,failure_id,module,repo,severity,similarity,score\n${csv}`], { type: 'text/csv' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `debug_queue_${runId}.csv`;
            a.click();
          }}
        >
          Export CSV
        </button>
      </div>
    </div>
  );
}

export default DebugQueue;
