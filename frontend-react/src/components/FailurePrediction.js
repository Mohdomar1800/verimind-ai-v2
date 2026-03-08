import React, { useState, useEffect } from 'react';
import { getContext, getExplanation } from '../services/api';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts';

function FailurePrediction({ runId, featureRunId, onBack }) {
  const [predictions, setPredictions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedItem, setSelectedItem] = useState(null);
  const [explanation, setExplanation] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchData();
  }, [runId]);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getContext(runId);
      const items = (data.items || [])
        .filter(i => i.risk_level === 'high' || i.risk_level === 'medium')
        .sort((a, b) => b.context_score - a.context_score);
      setPredictions(items);
    } catch (error) {
      console.error('Failed to fetch predictions:', error);
      setError('Backend is down or data unavailable.');
    }
    setLoading(false);
  };



  const handleRowClick = async (item) => {
    setSelectedItem(item);
    try {
      const exp = await getExplanation(item.item_id || `${item.repo}:${item.module_name}`);
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

  const chartData = predictions.slice(0, 10).map(p => ({
    name: (p.module_name || '').slice(0, 10),
    score: p.context_score,
    risk: p.risk_level,
  }));

  const getBarColor = (risk) => {
    return risk === 'high' ? '#FB5E4C' : '#F7CC66';
  };

  if (loading) {
    return (
      <div className="main-container">
        <div className="loading">Analyzing failure risks...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="main-container">
        <div className="page-header">
          <h1 className="page-title">Failure Risk Prediction</h1>
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
          <h1 className="page-title">Failure Risk Analysis</h1>
          <p style={{ color: '#444', fontSize: '13px', marginTop: '-5px' }}>
            Context: <span style={{ fontWeight: '600' }}>{runId}</span> | Model: <span style={{ fontWeight: '600' }}>{featureRunId}</span>
          </p>
        </div>
        <div style={{ display: 'flex', gap: '10px' }}>
          <button className="btn btn-secondary" onClick={fetchData}>Refresh Results</button>
          <button className="back-btn" onClick={onBack}>← Go Back</button>
        </div>
      </div>

      <p style={{ marginBottom: '20px', fontSize: '16px' }}>
        AI-powered prediction of which modules are most likely to fail based on historical verification data.
      </p>

      {/* Heatmap Visualization */}
      <div className="chart-container">
        <h3 className="chart-title">Failure Risk Heatmap</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '8px', marginTop: '15px' }}>
          {predictions.slice(0, 12).map((item, idx) => (
            <div
              key={idx}
              className={`heatmap-cell ${item.risk_level}`}
              onClick={() => handleRowClick(item)}
              style={{ padding: '15px', cursor: 'pointer' }}
            >
              <div style={{ fontWeight: 600 }}>{item.module_name}</div>
              <div style={{ fontSize: '12px' }}>{item.repo || 'unknown'}</div>
              <div style={{ fontSize: '18px', fontWeight: 700, marginTop: '5px' }}>
                {(item.context_score || 0).toFixed(1)}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="two-columns" style={{ marginTop: '20px' }}>
        {/* Chart */}
        <div className="chart-container">
          <h3 className="chart-title">Top Risk Modules</h3>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={chartData} layout="vertical">
              <XAxis type="number" domain={[0, 100]} />
              <YAxis dataKey="name" type="category" width={80} tick={{ fontSize: 12 }} />
              <Tooltip />
              <Bar dataKey="score" stroke="black" strokeWidth={1}>
                {chartData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={getBarColor(entry.risk)} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Explanation Panel */}
        <div className="chart-container">
          <h3 className="chart-title">Evidence & Explanation</h3>
          {selectedItem ? (
            <div className="evidence-panel">
              <h4 style={{ marginBottom: '15px' }}>
                {selectedItem.repo}:{selectedItem.module_name}
              </h4>
              {explanation && (
                <>
                  <div style={{ marginBottom: '15px' }}>
                    <strong>Why this prediction?</strong>
                    <ul style={{ marginTop: '10px', paddingLeft: '20px' }}>
                      {explanation.reasons?.map((r, i) => (
                        <li key={i} style={{ marginBottom: '5px' }}>{r}</li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <strong>Evidence:</strong>
                    {Object.entries(explanation.evidence || {}).map(([key, val]) => (
                      <div key={key} className="evidence-item">
                        <span className="evidence-label">{key}</span>
                        <span className="evidence-value">{typeof val === 'number' ? val.toFixed(2) : val}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          ) : (
            <div className="empty-state">Click on a module to see explanation</div>
          )}
        </div>
      </div>

      {/* Data Table */}
      <div className="data-table-container" style={{ marginTop: '20px' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Rank</th>
              <th>Module</th>
              <th>Repository</th>
              <th>Risk Score</th>
              <th>Risk Level</th>
            </tr>
          </thead>
          <tbody>
            {predictions.map((item, idx) => (
              <tr key={idx} onClick={() => handleRowClick(item)} style={{ cursor: 'pointer' }}>
                <td>{idx + 1}</td>
                <td><strong>{item.module_name || 'unknown'}</strong></td>
                <td>{item.repo || 'unknown'}</td>
                <td>{(item.context_score || 0).toFixed(1)}</td>
                <td>
                  <span className={`risk-badge ${item.risk_level}`}>
                    {item.risk_level.toUpperCase()}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Export Button */}
      <div style={{ marginTop: '20px', textAlign: 'right' }}>
        <button
          className="btn btn-secondary"
          onClick={() => {
            const csv = predictions.map(p =>
              `${p.repo},${p.module_name},${p.context_score},${p.risk_level}`
            ).join('\n');
            const blob = new Blob([`repo,module,score,risk\n${csv}`], { type: 'text/csv' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `failure_predictions_${runId}.csv`;
            a.click();
          }}
        >
          Export CSV
        </button>
      </div>
    </div>
  );
}

export default FailurePrediction;
