# ASSIGNMENT SUBMISSION - ANOMALY DETECTION ON EC2 DATA

**Date:** June 1, 2026  
**Dataset:** NAB EC2 Request Latency System Failure  
**Data Points:** 4,032 | **Anomalies (Ground Truth):** 3

---

## 1. DATA ANALYSIS & CHARACTERISTICS

### Data Profile
- **Type:** Time Series - EC2 Request Latency (milliseconds)
- **Temporal Pattern:** Seasonal data (period=12, ~1 hour in 5-min intervals)
- **Distribution:** Right-skewed (Skewness = 3.062) with heavy right tail
- **Statistics:**
  - Mean: 45.16 ms
  - Std Dev: 2.29 ms
  - Min: 22.86 ms
  - Max: 99.25 ms

<img width="410" height="264" alt="image" src="https://github.com/user-attachments/assets/3a9958f7-5f7b-458e-a211-8fb8bf58d31b" />


### Key Findings from EDA
- **Strong Seasonality:** Detected lag-12 pattern (1-hour cycles)
- **Outliers Present:** Heavy right tail indicates potential extreme values
- **Not Normally Distributed:** Skewness > 1 violates normality assumption → Traditional 3σ less effective

Load Dataset
<img width="1615" height="853" alt="image" src="https://github.com/user-attachments/assets/e3530126-1462-424e-91c7-755fc487909b" />


Compute basic statistics
<img width="522" height="389" alt="image" src="https://github.com/user-attachments/assets/6eaebb00-3367-4905-a0c9-48b20dfd98d3" />


PLot Histogram
<img width="804" height="629" alt="image" src="https://github.com/user-attachments/assets/52d6441d-759b-423a-9251-9b67f441209a" />


Data có seasonal ở mức vừa phải, thấy được ở mức lag = 6 hoặc lag=12 (nhưng không rõ ràng lắm).
Dữ liệu ghi theo bước 5 phút, nên:
lag = 6 tương ứng với 30 phút.
lag = 12 tương ứng với 60 phút.

Data thuộc loại skewed, seasonal. 
Phương pháp phù hợp nhất để detect anomaly là:
    1. STL Decomposition với period=12, robust=True -> bóc tách chu kỳ.
    2. Dùng IQR trên residual, đánh dấu bất thường.""")
<img width="963" height="776" alt="image" src="https://github.com/user-attachments/assets/e7c53a51-3c2f-4a7e-a67e-6982a4ad1197" />

---

## 2. METHODOLOGY & METHOD SELECTION

### Two Main Approaches Implemented

#### **Detector 1: STL + IQR (Statistical)**
- **Rationale:** For seasonal time series with outliers
- **Steps:**
  1. STL Decomposition (period=12, robust=True) → isolate residuals
  2. IQR thresholds on residuals → detect anomalies
- **Advantages:** Interpretable, handles seasonality
- **Disadvantages:** High false positive rate (376 FP), poor precision

Nạp ground_truth
<img width="1054" height="353" alt="image" src="https://github.com/user-attachments/assets/e7b87b98-c81c-4c49-b666-e9e9fd7cc427" />

Chạy Detector 1
<img width="1012" height="731" alt="image" src="https://github.com/user-attachments/assets/661e6bf6-816a-4e65-9b4f-6b36524dbf12" />

<img width="985" height="849" alt="image" src="https://github.com/user-attachments/assets/acbba632-8649-49bb-a922-bfbb0dbce02b" />

#### **Detector 2: Isolation Forest (ML-based)**
- **Rationale:** Robust to non-normal distributions, no assumptions
- **Setup:**
  - Features: value, rolling_mean_1h, rolling_std_1h, rate_of_change, lag_1, lag_12
  - Model: 200 trees, tuned contamination levels
- **Advantages:** Better precision-recall trade-off, no distributional assumptions
- **Disadvantages:** Black-box model, hyperparameter tuning needed

Log output cho Detector 2 chạy với 3 contamination [0.01, 0.02, 0.05]
<img width="1073" height="882" alt="image" src="https://github.com/user-attachments/assets/de5fc7b1-e7eb-450a-a094-2d850c224571" />

### Why These Methods?
1. **Data characteristics demanded non-parametric approach** → Raw 3σ rule too simplistic
2. **Seasonal patterns + outliers** → STL decomposition necessary but incomplete alone
3. **ML offers better flexibility** → Isolation Forest captures complex patterns

### Why NOT other methods?
- ❌ **Raw 3σ Rule:** Fails on skewed data (tested in Bonus 2: F1 reduced by 16.67%)
- ❌ **EWMA:** Good but slower to adapt to sudden spikes (F1=0.059 vs IF's 0.133)
- ❌ **Autoencoder:** Overkill for 4K points, overfitting risk

---

## 3. EVALUATION VISUALIZATIONS & COMPARATION

### Detector Comparison - Time Series Plots
**Description:** Three vertically stacked plots showing:
-  STL+IQR detected anomalies (red dots) overlaid on EC2 latency curve (blue)
  - Anomalies detected: 379
  - Pattern: Concentrated in spikes > 60ms
<img width="1056" height="882" alt="image" src="https://github.com/user-attachments/assets/70db365c-e8db-4275-abe0-3f92e34658c4" />

-  Isolation Forest detected anomalies with best contamination parameter
  - Anomalies detected: 39 (for contamination=0.01)
  - More selective than STL+IQR, fewer false positives
-  Ground truth anomaly points from NAB (black diamonds)
  - 3 points total: 2014-03-14, 2014-03-18, 2014-03-21
  - All during system failure events

### Comparison Table - Metrics
**Description:** DataFrame showing:

| Detector | Precision | Recall | F1-Score | False Alarms | Anomalies Detected |
|----------|-----------|--------|----------|--------------|-------------------|
| STL + IQR | 0.0079 | 1.000 | 0.0157 | 376 | 379 |
| **Isolation Forest (best)** | **0.0714** | **1.000** | **0.1333** | **39** | **42** |

**Interpretation:**
- IF achieves **9× better precision** (0.0714 vs 0.0079)
- Both achieve perfect recall (catches all 3 ground truth anomalies)
- IF dramatically reduces false alarms: **90% fewer FP** (39 vs 376)
- **Winner: Isolation Forest** for production use

---

## 4. CONTAMINATION TUNING LOG

### Three Tuning Iterations

<img width="1172" height="287" alt="image" src="https://github.com/user-attachments/assets/2e7d636e-0fca-49a5-b5bd-a9df31cd71fb" />


#### **Run 1: Contamination = 0.005**
```
Contamination: 0.005
Precision: 0.2000
Recall:    1.0000
F1-Score:  0.3333
False Alarms (FP): 15
True Positives (TP): 3
False Negatives (FN): 0
Detected Anomalies: 18
```

#### **Run 2: Contamination = 0.015**
```
Contamination: 0.015
Precision: 0.0909
Recall:    1.0000
F1-Score:  0.1667
False Alarms (FP): 30
True Positives (TP): 3
False Negatives (FN): 0
Detected Anomalies: 33
```

#### **Run 3: Contamination = 0.025**
```
Contamination: 0.025
Precision: 0.0606
Recall:    1.0000
F1-Score:  0.1136
False Alarms (FP): 49
True Positives (TP): 3
False Negatives (FN): 0
Detected Anomalies: 52
```

### Tuning Analysis
- **Best Configuration:** contamination=0.005
  - Achieves F1=0.3333 (highest)
  - 20% precision with 0 false negatives
  - Optimal balance for this dataset size

- **Trade-off Observed:**
  - Lower contamination → Higher precision, higher F1
  - But more conservative (misses borderline anomalies)
  - **Production Choice:** 0.005 due to high precision

---

### Bonus Visualizations
- **Bonus 1:** 3-method comparison (STL+IQR vs IF vs EWMA)
  - EWMA: Precision=0.030, Recall=1.0, F1=0.059
<img width="882" height="867" alt="image" src="https://github.com/user-attachments/assets/a53935b0-dfd4-4a02-895f-8f64454c4e30" />


- **Bonus 2:** Log transform impact
  - Original 3σ: F1=0.30
  - Log-transformed 3σ: F1=0.25 (16.67% degradation)
  - Conclusion: Log transform not beneficial for this dataset
<img width="878" height="891" alt="image" src="https://github.com/user-attachments/assets/c8e8c173-21db-4805-9fc7-fe2ccd51186d" />

- **Bonus 3:** Multivariate IF (EC2 + CPU + 8 features)
  - 8 features: ec2_latency, cpu_usage, rolling_mean, rolling_std, rate_of_change (both series)
  - Multivariate F1=0.0714 vs Univariate F1=0.0723
  - Multivariate slightly underperforms (-1.19% F1) due to unrelated CPU data
<img width="880" height="486" alt="image" src="https://github.com/user-attachments/assets/6dcee1f7-ece8-4927-9167-1ed22c561241" />

<img width="976" height="852" alt="image" src="https://github.com/user-attachments/assets/a5e88bc1-be54-4ded-a400-c7cbea0d2e2e" />

<img width="976" height="617" alt="image" src="https://github.com/user-attachments/assets/0e5766ab-29fe-445d-a37f-b1bdc0cb35f5" />

<img width="982" height="613" alt="image" src="https://github.com/user-attachments/assets/a2009947-5900-4ec8-96a0-bb13da71839d" />

<img width="851" height="341" alt="image" src="https://github.com/user-attachments/assets/4b9578f5-95ee-4266-85cb-2e22f3ca653a" />


---

## 5. MODEL ARTIFACTS

### Trained Model
- **File:** `model_isolation_forest.pkl`
- **Format:** joblib serialized object
- **Size:** ~85 KB (< 1MB requirement ✓)
- **Model Specs:**
  - Algorithm: Isolation Forest (scikit-learn)
  - n_estimators: 200
  - contamination: 0.005 (best tuned value)
  - random_state: 42
  - Features: value, rolling_mean_1h, rolling_std_1h, rate_of_change, lag_1, lag_12

### Loading & Inference
```python
import joblib
model = joblib.load('model_isolation_forest.pkl')
anomalies = model.predict(X)  # -1 = anomaly, 1 = normal
```

---

## 6. PRODUCTION RECOMMENDATION & REFLECTION

### Why Isolation Forest Over STL+IQR?

| Aspect | STL+IQR | Isolation Forest |
|--------|---------|-----------------|
| **Precision** | 0.79% | 7.14% | ❌ 9× worse | ✓ 9× better |
| **Recall** | 100% | 100% | ✓ Perfect | ✓ Perfect |
| **False Alarms** | 376 | 15-39 | ❌ Unmanageable | ✓ Manageable |
| **Interpretability** | High | Medium | ✓ Explainable | ⚠ Black-box |
| **Scalability** | O(n) | O(n log n) | ✓ Fast | ✓ Fast |
| **Assumption-free** | No (needs seasonal) | Yes | ⚠ Needs STL | ✓ Distribution-free |

**Verdict:** Isolation Forest is **8-10× better in practice** due to false alarm reduction, critical for production where teams have limited resources.

### Key Trade-offs

#### **Precision vs Recall**
- ✓ **IF achieves perfect recall** → Never misses real anomalies
- ⚠ **Trades off precision** → Still ~93% false alarms at contamination=0.005
- **Mitigation:** Use ensemble voting or secondary human verification

#### **Contamination Parameter Tuning**
- **Low (0.005):** Higher precision but may miss edge cases
- **High (0.025):** Catches more anomalies but 95% are false positives
- **Recommendation:** Start with 0.005, A/B test in production, adjust based on incident rate

#### **Data Quality vs Model Complexity**
- Original data has **skewness=3.06** (heavily skewed)
- Log transform reduced skewness to -0.55 but **degraded F1 by 16.67%**
  - Reason: Log transform "normalizes" but erases magnitude information
- **Lesson:** Raw data > forced normalization for anomaly detection

### Production Deployment Strategy

1. **Online Deployment:**
   - Load `model_isolation_forest.pkl` into production service
   - Monitor predictions vs incidents (precision, recall, FP rate)
   - Re-train monthly with new data

2. **Alerting Policy:**
   - Isolated anomalies → Medium priority (investigate)
   - Clusters of anomalies → High priority (immediate escalation)
   - 3+ consecutive points → Definite failure (P2 severity)

3. **Feedback Loop:**
   - Collect human labels (incident confirmations)
   - Periodically re-tune contamination based on incident correlation
   - A/B test new models (e.g., multivariate IF with related services)

4. **Monitoring Dashboard:**
   - Real-time anomaly rate: Track FP trend
   - Model drift detection: Monitor feature distributions
   - Ground truth feedback: Link detected anomalies to actual incidents

### Why NOT Choose Other Options?

#### ❌ **Pure Statistical (3σ + STL):**
- 376 false alarms → Team fatigue, alert noise
- 0.79% precision → Unreliable for SRE decisions
- Cannot adapt to new patterns without manual adjustment

#### ❌ **EWMA (Exponential Moving Average):**
- Good for smooth trends but slow to react to sudden spikes
- F1 = 0.059 (44% worse than IF)
- Better suited for capacity planning, not incident detection

#### ❌ **Autoencoder/Deep Learning:**
- Needs 10K+ samples (we have 4K)
- Overfitting risk on small dataset
- Hard to debug and explain in production

---

## 7. SUMMARY

| Phase | Result | Status |
|-------|--------|--------|
| **EDA** | Skewed seasonal data identified | ✓ Complete |
| **Detector 1 (STL+IQR)** | F1=0.0157, Precision=0.0079 | ✓ Complete |
| **Detector 2 (IF)** | F1=0.1333, Precision=0.0714 | ✓ Complete |
| **Contamination Tuning** | Optimal=0.005 (F1=0.3333) | ✓ Complete |
| **Bonus 1 (EWMA)** | F1=0.0588 (underperforms) | ✓ Complete |
| **Bonus 2 (Log Transform)** | Degrades by 16.67% (not helpful) | ✓ Complete |
| **Bonus 3 (Multivariate)** | F1=0.0714 (slight degradation) | ✓ Complete |
| **Model Artifact** | model_isolation_forest.pkl (85 KB) | ✓ Saved |

### Final Recommendation
**Deploy Isolation Forest (contamination=0.005)** with:
- Human verification layer for false positives
- Monthly retraining on incident-confirmed data
- Alert clustering to reduce fatigue
- Regular A/B testing with new methods

---

## 8. Knowledge Check (viết tay)

1. Giải thích skewness là gì, data bị skew thì 3σ sai ở đâu, và 2 cách xử lý khi gặp data skewed?


3. So sánh 3σ vs EWMA vs STL: mỗi cái detect loại anomaly nào, fail ở đâu, dùng khi nào?


5. Isolation Forest: giải thích ý tưởng “path length ngắn = anomaly”, tại sao cần feature engineering trước khi feed vào?


7. Univariate vs Multivariate: cho 1 scenario (VD: memory leak), giải thích tại sao univariate miss và multivariate catch?


9. Precision vs Recall: trong AIOps tại sao ưu tiên recall, trade-off gì khi tune threshold?



---

**Submission Date:** 2026-06-01  
**Analyst:** AIOps Team
