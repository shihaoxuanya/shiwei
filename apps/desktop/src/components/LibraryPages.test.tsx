import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HomePage, LibraryPage, sourceStatus } from "./LibraryPages";
import { searchLocal } from "../lib/search";
import { deleteSource, reindexSource, type SourceSummary } from "../lib/import";
import { useImportStore } from "../stores/import-store";
vi.mock('../lib/search',()=>({searchLocal:vi.fn(async()=>[])}));
vi.mock('../lib/import',async original=>({...await original<typeof import('../lib/import')>(), deleteSource:vi.fn(async()=>{}),reindexSource:vi.fn(async()=>{}),openSource:vi.fn(async()=>{}),revealSource:vi.fn(async()=>{})}));
const source:SourceSummary={id:'f1',originalPath:'C:/outside/原件.txt',storedPath:'C:/test/raw/file',filename:'验收报告.txt',contentHash:'hash',size:4096,importedAt:'2026-09-08T01:00:00Z',status:'searchable'};
const props={sources:[source],loading:false,error:'',onRefresh:vi.fn()};
beforeEach(()=>{vi.clearAllMocks();useImportStore.setState({status:'idle',report:null,error:null,progress:null});});
it('uses the same import actions on Home and Library without a home composer',()=>{
  const addFiles=vi.fn(async()=>{});useImportStore.setState({addFiles});
  const view=render(<HomePage {...props} notes={[]} onNewNote={vi.fn()} onOpenNote={vi.fn()} onPrivacy={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button',{name:'添加文件'}));expect(addFiles).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('textbox',{name:'消息'})).not.toBeInTheDocument();
  view.unmount();render(<LibraryPage {...props}/>);fireEvent.click(screen.getByRole('button',{name:'添加文件'}));expect(addFiles).toHaveBeenCalledTimes(2);
});
it('requests body-only Chinese/technical terms from the search adapter and deduplicates file rows',async()=>{
  vi.mocked(searchLocal).mockResolvedValue([{chunkId:'c1',documentId:'d1',sourceId:'f1',filename:source.filename,documentTitle:source.filename,content:'Oracle 数据库迁移，单次全量 38～50 天。',lexicalScore:.1,matchedBy:['chunks_fts'],pageNumber:2},{chunkId:'c2',documentId:'d1',sourceId:'f1',filename:source.filename,documentTitle:source.filename,content:'Oracle 增量追平 7～14 天。',lexicalScore:.08,matchedBy:['chunks_fts']}]);
  render(<LibraryPage {...props}/>);fireEvent.change(screen.getByRole('textbox',{name:'搜索资料名称或正文'}),{target:{value:'Oracle'}});
  await waitFor(()=>expect(searchLocal).toHaveBeenCalledWith('Oracle',100,'imported_file'));
  expect(await screen.findByText('第 2 页')).toBeInTheDocument();expect(screen.getAllByRole('article')).toHaveLength(1);expect(screen.getAllByText('Oracle',{selector:'mark'})).toHaveLength(2);
});
it('does not claim no results while body search is still pending', async () => {
  let finish!: (value: []) => void;
  vi.mocked(searchLocal).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  render(<LibraryPage {...props}/>);
  fireEvent.change(screen.getByRole('textbox',{name:'搜索资料名称或正文'}),{target:{value:'尚未查询完的正文词'}});
  expect(screen.getByText('正在查找资料…')).toBeVisible();
  expect(screen.queryByText('没有找到匹配资料')).not.toBeInTheDocument();
  await waitFor(()=>expect(searchLocal).toHaveBeenCalledWith('尚未查询完的正文词',100,'imported_file'));
  await act(async () => { finish([]); });
  expect(await screen.findByText('没有找到匹配资料')).toBeVisible();
  expect(screen.queryByText('正在查找资料…')).not.toBeInTheDocument();
});
it('renders search text safely without treating source HTML as markup',async()=>{
  vi.mocked(searchLocal).mockResolvedValue([{chunkId:'x',documentId:'d',sourceId:'f1',filename:source.filename,documentTitle:source.filename,content:'<img src=x onerror=alert(1)>',lexicalScore:1,matchedBy:[]}]);
  const view=render(<LibraryPage {...props}/>);fireEvent.change(screen.getByRole('textbox'),{target:{value:'img'}});await screen.findByText('img',{selector:'mark'});expect(view.container.querySelector('img')).toBeNull();
});
it('keeps file operations accessible and deletes by managed source identity only',async()=>{
  vi.spyOn(window,'confirm').mockReturnValue(true);render(<LibraryPage {...props}/>);
  fireEvent.click(screen.getByRole('button',{name:'验收报告.txt的更多操作'}));
  fireEvent.click(screen.getByRole('button',{name:'删除导入副本'}));
  await waitFor(()=>expect(deleteSource).toHaveBeenCalledWith('f1'));expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('原始位置的文件不会删除'));
});
it('exposes actual failed source reason and retry, never calls saved equivalent to semantic ready',async()=>{
  const failed={...source,status:'failed' as const,error:'PDF 已损坏，请重新导入'};
  render(<LibraryPage {...props} sources={[failed]}/>);expect(screen.getByText(failed.error)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'验收报告.txt的更多操作'}));fireEvent.click(screen.getByRole('button',{name:'重新处理'}));await waitFor(()=>expect(reindexSource).toHaveBeenCalledWith('f1'));
  expect(sourceStatus(source)).toBe('可按关键词查找');expect(sourceStatus({...source,retrieval:{keyword:'ready',semantic:'requires_rebuild'}})).not.toBe('已就绪');
});
it('shows a compact real recent list, no fake recent contents for an empty library',()=>{
  const v=render(<HomePage {...props} notes={[]} onNewNote={vi.fn()} onOpenNote={vi.fn()} onPrivacy={vi.fn()}/>);expect(screen.getByText('最近留下的')).toBeInTheDocument();expect(screen.getByText(source.filename)).toBeInTheDocument();
  v.rerender(<HomePage {...props} sources={[]} notes={[]} onNewNote={vi.fn()} onOpenNote={vi.fn()} onPrivacy={vi.fn()}/>);expect(screen.queryByText('最近留下的')).not.toBeInTheDocument();expect(screen.getByRole('button',{name:'写一条笔记'})).toBeInTheDocument();
});
