# Issue tracker：GitHub

這個 repo 的 issue 與規格都住在 GitHub Issues（`ycpss91255-research/config_manager`），
一律以 `gh` CLI 操作。repo 由 `git remote -v` 推得，在 clone 內執行時 `gh` 會自己認出來。

## 慣例

- **開 issue**：`gh issue create --title "..." --body-file <檔案>`
  多行內文用 `--body-file` 而非 `--body`：shell 會把內文裡的 `>` 當成重導向，實際發生過
  （內文被截斷、只剩一半）。
- **讀 issue**：`gh issue view <編號> --comments`
- **列 issue**：`gh issue list --milestone v0.1.0 --state open --json number,title,labels`
- **留言**：`gh issue comment <編號> --body-file <檔案>`
- **標籤**：`gh issue edit <編號> --add-label "..."` / `--remove-label "..."`
- **關閉**：`gh issue close <編號> --reason completed`

## 這個 repo 額外的規矩

以下不是 GitHub 的慣例，是這個 repo 的（見 `CLAUDE.md`）：

- **決策要寫回它被提出的地方。** 討論定案後，把決策、佐證、以及被否決的選項與理由寫回
  該 issue 或 PR，不要只留在對話裡。
- **關閉帶驗收條件的 issue 時，證據附在 issue 上，勾選框實際勾起來。** 用
  `gh issue edit --body-file` 改寫內文把 `- [ ]` 改成 `- [x]`，另外留一則證據留言。
- **依 milestone 與 issue 推進**，照 GitHub milestone 的順序做，不要跳版。
- **自己無法決定的事**（owner 層級／架構）→ 開 issue 並等待，但不要空轉：同時去做另一個
  沒被擋住的 issue。

## PR 作為請求來源

**PRs as a request surface: no.** _（若這個 repo 要把外部 PR 也納入 triage 佇列，改成 `yes`；`/triage` 讀這個旗標。）_

目前所有 PR 都由維護者自己開，來源是 issue 而不是外部貢獻，所以不納入。
