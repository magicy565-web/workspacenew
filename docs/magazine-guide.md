# 买手杂志应用

在运行中的工作区打开 `/magazines`。这个应用使用原始 Buyer Magazine Studio v1.1.0 的确定性渲染器；研究、编辑和素材选择由调用方 Agent 完成。

1. 创建项目，填写企业 HTTPS 官网和制作要求。保存网址本身不会抓取网站。
2. 让已连接 Agent 阅读 [SKILL.md](../engine/SKILL.md)，研究真实来源，准备有来源标注的完整简报与图片。可以用 `get_editorial_example` 获取虚构的格式示例，不能把示例当作真实调研。
3. 通过 MCP 的 `save_editorial_brief` 和 `upload_image` 提交资料；也可使用网页中的编辑及上传入口。
4. 调整封面布局和配色，生成杂志。审查实际 HTML 预览及来源记录，再下载 ZIP。ZIP 包含当前保存构建的 HTML、样式、脚本、字体、图片和来源清单。

修改资料会使旧构建显示需要重新生成。生成结果是内部审阅作品，不会自动发布或发送询盘。图片授权状态、采购条款和来源真实性需要按技能工作流核对，不能由版式生成推断。

现有 MCP 工具为 `list_projects`、`create_project`、`get_project`、`add_instruction`、`get_editorial_example`、`upload_image`、`save_editorial_brief`、`build_magazine`。它们与网页使用同一数据目录。

将本项目作为独立技能使用时，可把完整 `engine/` 复制到宿主的 `buyer-magazine-studio/` 技能目录，保留 `SKILL.md`、`scripts/`、`skills/` 和 `assets/`。Web 工作区和 MCP 服务需要完整工程。
