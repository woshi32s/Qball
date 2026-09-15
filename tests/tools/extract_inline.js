// 提取 HTML 里的内联 <script> 到独立文件(供 node --check 做语法检查)
// 用法: node tools/extract_inline.js <input.html> <output.js>
const fs = require('fs');
const input = process.argv[2];
const output = process.argv[3];
if (!input || !output) {
  console.error('usage: node extract_inline.js <input.html> <output.js>');
  process.exit(2);
}
const html = fs.readFileSync(input, 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) {
  console.error('no inline script found in ' + input);
  process.exit(1);
}
fs.writeFileSync(output, m[1]);
console.log('extracted ' + m[1].length + ' chars -> ' + output);
