"""JSON command-line interface for agent hosts that do not support MCP."""
import argparse
import base64
import json
from pathlib import Path
import sys
from . import core


def main():
    parser = argparse.ArgumentParser(description='Buyer Magazine Studio JSON CLI')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    create = sub.add_parser('create'); create.add_argument('--name', required=True); create.add_argument('--website', required=True); create.add_argument('--request', default='')
    get = sub.add_parser('get'); get.add_argument('project_id')
    message = sub.add_parser('add-instruction'); message.add_argument('project_id'); message.add_argument('text')
    brief = sub.add_parser('save-brief'); brief.add_argument('project_id'); brief.add_argument('file')
    image = sub.add_parser('upload-image'); image.add_argument('project_id'); image.add_argument('file')
    build = sub.add_parser('build'); build.add_argument('project_id')
    export = sub.add_parser('download'); export.add_argument('project_id'); export.add_argument('--out', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'list': result = core.list_projects()
        elif args.command == 'create': result = core.create_project(args.name, args.website, args.request)
        elif args.command == 'get': result = core.get_project(args.project_id)
        elif args.command == 'add-instruction': result = core.add_instruction(args.project_id, args.text)
        elif args.command == 'save-brief': result = core.save_brief(args.project_id, json.loads(Path(args.file).read_text(encoding='utf-8')))
        elif args.command == 'upload-image':
            path = Path(args.file)
            if path.stat().st_size > core.MAX_IMAGE: raise ValueError('图片超过 8 MB')
            result = core.save_image(args.project_id, path.name, base64.b64encode(path.read_bytes()).decode('ascii'))
        elif args.command == 'build': result = core.build_project(args.project_id)
        else:
            output = Path(args.out)
            output.write_bytes(core.download_zip(args.project_id))
            result = {'file': str(output.resolve()), 'bytes': output.stat().st_size}
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
