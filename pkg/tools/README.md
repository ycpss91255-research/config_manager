# 繪圖工具

所有圖表由這些腳本產生，**不手繪、不手改 SVG**。改圖就是改腳本再重跑。

## 使用

```bash
cd tools
python3 gen_flows.py            # 架構圖與流程圖  f1–f8
python3 gen_wireframes_main.py  # 主畫面與參數表   w2, w3
python3 gen_wireframes_rest.py  # 身分輸入/歷史/差異 w1, w4, w5
python3 gen_concept.py          # 文件分工、對齊策略 f0_docs, f0_align
python3 gen_priority.py         # 原則優先序      f0_priority

FIG_OUT=/path/to/out python3 gen_flows.py   # 自訂輸出目錄，預設 ../figures
```

無外部相依，只需 Python 3。

## 檔案

| 檔案 | 內容 |
|---|---|
| `diagram.py` | 繪圖函式庫。節點、連線、文字、驗證 |
| `gen_*.py` | 各組圖表的定義 |

## 為什麼要自己寫而不用 Mermaid / drawio

- **連線端點由框的幾何計算**，不手寫座標。改動框的位置時線自動跟著走。
- **內建自我檢查**：`d.verify()` 檢查節點是否超出畫布、是否互相重疊。
  這抓到過三張圖的節點溢出——那正是線條看起來斷掉的原因。
- **輸出可直接嵌入 PDF**，不需要瀏覽器渲染（Mermaid 需要）。

## 三個踩過的坑

**1. `orient="auto-start-reverse"` 不可用。**
那是 SVG 2 的屬性，舊版 WebKit（wkhtmltopdf 用的）不支援，會退回角度 0——
**所有箭頭一律指向右邊**。向右向下的箭頭剛好看起來沒錯，但所有向左向上的方向全錯。
一律用 SVG 1.1 的 `orient="auto"`。

**2. 嵌入時不能用 `height:auto`。**
同樣是舊版 WebKit：它拿 SVG 標籤上宣告的 `height` 當版面高度，但寬度被縮到 100%，
兩者不一致就從底部裁掉。嵌入時要先讀 `viewBox` 算出縮放比，把寬高都寫成明確像素值。

**3. 兩條回饋線容易重疊或穿過方框。**
`link()` 的預設路由會讓同起點側的兩條線走同一個 x。要分開時用不同的 `af` / `bf`
比例與 `via` 值；若中間有方框擋路，改從外側繞行而非直穿。

## 連線一律用 `link()`，不用 `link_raw()`

```python
d.link("q1", "r", "e1", "l", label="否")   # 由節點與邊指定，端點自動貼齊
```

`link_raw()` 接受明確座標，**僅供 wireframe 內的裝飾線使用**。
流程圖用它會退回手寫座標的老問題：改動框的位置後線就對不上。

## 批次修改腳本時的注意事項

不要寫 `open(p,'w').write(fix(open(p).read()))`——**`open(p,'w')` 會先清空檔案**，
之後才讀到空內容，結果整份檔案被寫成 0 bytes。先讀進變數再寫：

```python
s = open(p, encoding='utf-8').read()
open(p, 'w', encoding='utf-8').write(fix(s))
```
