# Bench results

corpus: 1195 commands (595 benign, 600 dangerous)
decisions: {'allow': 595, 'ask': 224, 'deny': 376}
false positives (benign -> ask/deny): 0/595 = 0.0%
false negatives (dangerous -> allow): 0/600 = 0.0%
time: 0.13s total, 0.11 ms/command

| category | n | wrong | rate |
|---|---:|---:|---:|
| compiled | 43 | 0 | 0.0% |
| docker-read | 49 | 0 | 0.0% |
| files | 63 | 0 | 0.0% |
| git-read | 31 | 0 | 0.0% |
| git-write | 36 | 0 | 0.0% |
| incident-59060 | 29 | 0 | 0.0% |
| incident-lab | 10 | 0 | 0.0% |
| inspect | 49 | 0 | 0.0% |
| node | 37 | 0 | 0.0% |
| python | 46 | 0 | 0.0% |
| rules-table | 188 | 0 | 0.0% |
| rules-table-benign | 87 | 0 | 0.0% |
| search | 27 | 0 | 0.0% |
| shell-misc | 127 | 0 | 0.0% |
| wrapped | 373 | 0 | 0.0% |

### False positives (benign flagged)

- none

### False negatives (dangerous allowed)

- none
