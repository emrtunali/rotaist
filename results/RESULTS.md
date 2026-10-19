# Rotaist — results

Back-test on 22 held-out weekdays of January 2025, 250 synthetic orders per day, plans executed on that day's real İBB traffic.

## 4 vans

| Planner | On time (real) | On time (planned) | Late min/day | Duty h | ETA error (MAE) | ETA bias | Worst day |
|---|---:|---:|---:|---:|---:|---:|---:|
| OR-Tools · free-flow matrix | 82.9 % | 99.9 % | 1162 | 40.5 | 28.3 min | +28.3 min | 58.4 % |
| OR-Tools · typical daytime matrix | 99.1 % | 99.7 % | 9 | 39.7 | 2.8 min | +0.2 min | 94.0 % |
| Rotaist solver · typical daytime matrix | 98.5 % | 99.8 % | 16 | 38.3 | 5.1 min | -0.4 min | 94.8 % |
| Rotaist · forecast, time-dependent | 99.1 % | 99.7 % | 15 | 38.3 | 2.8 min | -0.6 min | 88.0 % |
| Oracle · actual traffic known in advance | 99.7 % | 99.7 % | 2 | 38.0 | 0.0 min | +0.0 min | 98.0 % |

## 5 vans

| Planner | On time (real) | On time (planned) | Late min/day | Duty h | ETA error (MAE) | ETA bias | Worst day |
|---|---:|---:|---:|---:|---:|---:|---:|
| OR-Tools · free-flow matrix | 83.1 % | 100.0 % | 1260 | 42.2 | 28.5 min | +28.5 min | 63.2 % |
| OR-Tools · typical daytime matrix | 99.2 % | 99.9 % | 8 | 40.1 | 2.9 min | +0.1 min | 97.2 % |
| Rotaist solver · typical daytime matrix | 98.6 % | 99.9 % | 15 | 36.6 | 4.9 min | +0.1 min | 96.8 % |
| Rotaist · forecast, time-dependent | 99.5 % | 99.9 % | 6 | 35.9 | 2.5 min | -0.9 min | 92.4 % |
| Oracle · actual traffic known in advance | 99.8 % | 99.8 % | 0 | 35.6 | 0.0 min | +0.0 min | 98.4 % |

## 6 vans

| Planner | On time (real) | On time (planned) | Late min/day | Duty h | ETA error (MAE) | ETA bias | Worst day |
|---|---:|---:|---:|---:|---:|---:|---:|
| OR-Tools · free-flow matrix | 84.2 % | 99.9 % | 1112 | 41.8 | 26.6 min | +26.6 min | 68.8 % |
| OR-Tools · typical daytime matrix | 99.4 % | 99.9 % | 4 | 40.4 | 2.8 min | +0.3 min | 97.6 % |
| Rotaist solver · typical daytime matrix | 98.7 % | 99.8 % | 16 | 34.8 | 4.5 min | +0.1 min | 96.0 % |
| Rotaist · forecast, time-dependent | 99.5 % | 99.9 % | 3 | 33.6 | 2.1 min | -0.7 min | 97.2 % |
| Oracle · actual traffic known in advance | 99.9 % | 99.9 % | 0 | 33.6 | 0.0 min | +0.0 min | 99.6 % |

## Day-ahead traffic forecast (January 2025, vehicle-weighted)

| Forecast | Speed MAE | at 08:00 | at 18:00 |
|---|---:|---:|---:|
| No traffic (free flow) | 18.64 km/h | 22.5 | 27.8 |
| Cell average, all day | 9.30 km/h | 10.7 | 12.9 |
| Same hour last week | 4.99 km/h | 6.0 | 5.0 |
| Hour-of-week profile | 3.70 km/h | 4.3 | 3.9 |
| Rotaist gradient boosting | 3.50 km/h | 3.8 | 3.6 |
