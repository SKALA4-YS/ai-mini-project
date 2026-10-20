# Runbook: DB Down 대응 절차

## 대표 증상
데이터베이스에 연결할 수 없거나 쿼리가 전부 실패한다. 애플리케이션 로그에 connection refused, too many connections, could not connect to server 같은 DB 연결 오류가 쏟아지고, 리플리케이션이 끊기거나 primary 노드가 응답하지 않는다. 키워드: DB 장애, 데이터베이스 다운, DB 연결 실패, failover.

## 대응 절차
1. 모니터링 알람으로 장애를 즉시 인지한다 (get_monitoring_status로 현재 상태 확인).
2. 커넥션 풀/리플리케이션 상태를 점검하고 필요 시 failover 노드로 전환한다.
3. 서비스 정상화 확인 후 원인 분석과 재발 방지 대책을 문서화한다.
