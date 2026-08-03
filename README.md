# Kaizen AI

> **AIで、日本の現場に "改善" を。**
> AIを駆使して、さまざまな業務の効率化を実現するコンサルティング会社の運営リポジトリです。

このリポジトリは Kaizen AI の**会社運営のハブ**です。事業計画・サービス内容・案件管理・各種テンプレート・ナレッジを一元管理します。

---

## 📁 ディレクトリ構成

| ディレクトリ | 内容 |
|---|---|
| [`docs/company/`](docs/company/) | 会社概要・ミッション・事業計画・**組織/ガバナンス** |
| [`docs/services/`](docs/services/) | サービスカタログ・料金体系 |
| [`docs/operations/`](docs/operations/) | 業務フロー・使用ツール |
| [`docs/sales/`](docs/sales/) | 営業資料（会社紹介デッキ） |
| [`clients/`](clients/) | 顧客案件の管理（案件ごとにフォルダを作成） |
| [`templates/`](templates/) | 提案書・報告書・チェックリストの雛形 |
| [`knowledge/`](knowledge/) | ナレッジベース（事例・プロンプト集・調査メモ） |

---

## 🚀 使い方

### 新しい案件を始めるとき
1. `clients/` に `YYYYMMDD_顧客名/` フォルダを作成
2. `templates/` から必要な雛形をコピー
3. [`clients/README.md`](clients/README.md) の手順に従って進める

### 提案書を作るとき
`templates/proposal-template.md` をコピーして編集

### サービス内容を確認するとき
[`docs/services/service-catalog.md`](docs/services/service-catalog.md) を参照

---

## 🏢 会社情報

- **会社名**: Kaizen AI（カイゼンAI）
- **事業内容**: AI活用による業務効率化コンサルティング
- **ミッション**: AIで、日本の現場に "改善" を。
- **組織体制**: オーナー（人間）× AI CEO（Claude）の二層経営 — [`docs/company/governance.md`](docs/company/governance.md)
- 詳細は [`docs/company/overview.md`](docs/company/overview.md) を参照

---

## ⚠️ 取り扱い注意

このリポジトリには顧客情報・機密情報が含まれます。**Private（非公開）** を維持してください。
- APIキー・パスワード・個人情報はコミットしない（`.gitignore` で除外）
- 顧客名は必要に応じて仮名・コードネームを使用
