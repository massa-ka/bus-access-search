# バスアクセスサーチ（パイロット版）

住所を起点に、指定距離内のバス停留所・事業者・営業所・系統・行先・曜日別便数を事業者横断で検索するStreamlitアプリです。

収録事業者：都営バス、鹿児島市営バス、北海道中央バス

## Streamlit Community Cloud

- Main file path: `app.py`
- Python dependencies: `requirements.txt`
- 検索用データ: `stops.csv.gz`, `stop_routes.csv.gz`

元のGTFSはこの公開用リポジトリには含めていません。営業所データのない事業者は「営業所情報なし」と表示します。
