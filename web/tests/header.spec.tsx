// web/tests/header.spec.tsx —— Header 项目名就地改名（任务1）
// 编辑态三态：button（可改名）→ input（Enter/失焦提交、Esc 取消）→ 回到展示；
// 提交口径：trim 非空且与当前名不同才上行（改名不允许清空/原地重发）。
import { render, screen, fireEvent } from "@testing-library/react";
import { Header } from "../src/components/layout/Header";

const NAME = "白族村落宗族组织变迁";

describe("Header 项目名编辑（任务1：≤30 字，用户可改）", () => {
  test("提供 onRename：项目名为按钮，点击进入编辑（maxLength 30、初值当前名）", () => {
    render(<Header projectName={NAME} onRename={() => {}} />);
    const btn = screen.getByRole("button", { name: NAME });
    expect(btn).toHaveAttribute("title", "点击修改项目名称");
    fireEvent.click(btn);
    const input = screen.getByLabelText("项目名称") as HTMLInputElement;
    expect(input.value).toBe(NAME);
    expect(input.getAttribute("maxlength")).toBe("30");
  });

  test("Enter 提交：trim 后上行 onRename，退出编辑态", () => {
    const renames: string[] = [];
    render(<Header projectName={NAME} onRename={(n) => renames.push(n)} />);
    fireEvent.click(screen.getByRole("button", { name: NAME }));
    const input = screen.getByLabelText("项目名称");
    fireEvent.change(input, { target: { value: "  宗族变迁调研  " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(renames).toEqual(["宗族变迁调研"]); // trim 后提交
    // 退出编辑态；展示仍为旧名（新名由父组件回读快照后生效）
    expect(screen.queryByLabelText("项目名称")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: NAME })).toBeInTheDocument();
  });

  test("Esc 取消：不上行，直接回到展示态", () => {
    const renames: string[] = [];
    render(<Header projectName={NAME} onRename={(n) => renames.push(n)} />);
    fireEvent.click(screen.getByRole("button", { name: NAME }));
    const input = screen.getByLabelText("项目名称");
    fireEvent.change(input, { target: { value: "不想改了" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(renames).toEqual([]);
    expect(screen.queryByLabelText("项目名称")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: NAME })).toBeInTheDocument();
  });

  test("空值/与当前名相同不提交（清空改名入口不允许置空）", () => {
    const renames: string[] = [];
    render(<Header projectName={NAME} onRename={(n) => renames.push(n)} />);
    fireEvent.click(screen.getByRole("button", { name: NAME }));
    const input = screen.getByLabelText("项目名称");
    fireEvent.change(input, { target: { value: "   " } }); // 空白
    fireEvent.keyDown(input, { key: "Enter" });
    expect(renames).toEqual([]);
    expect(screen.queryByLabelText("项目名称")).not.toBeInTheDocument();
    // 同名重发也不上行
    fireEvent.click(screen.getByRole("button", { name: NAME }));
    fireEvent.keyDown(screen.getByLabelText("项目名称"), { key: "Enter" }); // 初值=当前名
    expect(renames).toEqual([]);
  });

  test("失焦提交：blur 与 Enter 同一道提交逻辑", () => {
    const renames: string[] = [];
    render(<Header projectName={NAME} onRename={(n) => renames.push(n)} />);
    fireEvent.click(screen.getByRole("button", { name: NAME }));
    const input = screen.getByLabelText("项目名称");
    fireEvent.change(input, { target: { value: "宗族组织调研" } });
    fireEvent.blur(input);
    expect(renames).toEqual(["宗族组织调研"]);
  });

  test("未提供 onRename：项目名纯展示（span，无编辑入口）", () => {
    render(<Header projectName={NAME} />);
    const label = screen.getByText(NAME);
    expect(label.tagName).toBe("SPAN");
    fireEvent.click(label); // 点击不进入编辑
    expect(screen.queryByLabelText("项目名称")).not.toBeInTheDocument();
  });
});
