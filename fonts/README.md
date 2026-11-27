# HUD 글꼴

- 기본: `에이투지체-5Medium-Wide110.ttf` — 원본의 글자 윤곽과 간격을 가로 10% 확장한 파일
- 원본: `에이투지체-5Medium.ttf`
- 기본 경로·글자 크기·런타임 가로 배율: `prototype/hud_theme.py`

다른 가로 비율의 TTF는 `fonttools` 설치 후 다음 명령으로 생성한다.

```powershell
& .\.venv\Scripts\python.exe -m pip install fonttools
& .\.venv\Scripts\python.exe -m prototype.font_variant fonts/에이투지체-5Medium.ttf fonts/에이투지체-5Medium-Wide110.ttf 1.10
```

폰트 내부 저작권 표기는 `Copyright © 2026 PT&`이며 라이선스 표기는 SIL Open Font License 1.1이다.
