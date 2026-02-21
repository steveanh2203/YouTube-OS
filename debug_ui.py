import atomacos
import time
from collections import deque

def dump_ui():
    print("Finding CapCut...")
    try:
        app = atomacos.getAppRefByBundleId("com.lemon.lvoverseas")
        print(f"Found App: {app}")
    except Exception as e:
        print(f"Failed to find app: {e}")
        return

    print("\n=== START UI DUMP ===")
    for window in app.AXWindows:
        print(f"\nWindow: {getattr(window, 'AXTitle', 'NoTitle')} (Role: {getattr(window, 'AXRole', 'Unknown')})")
        print("-" * 50)
        
        # BFS Dump
        queue = deque([(window, 0)])
        visited = set()
        
        while queue:
            node, depth = queue.popleft()
            ref = getattr(node, 'ref', None)
            ident = id(ref) if ref else id(node)
            if ident in visited:
                continue
            visited.add(ident)
            
            indent = "  " * depth
            role = getattr(node, 'AXRole', 'Unknown')
            title = getattr(node, 'AXTitle', '')
            val = getattr(node, 'AXValue', '')
            desc = getattr(node, 'AXDescription', '')
            
            info_parts = []
            if title: info_parts.append(f"Title='{title}'")
            if val: info_parts.append(f"Value='{val}'")
            if desc: info_parts.append(f"Desc='{desc}'")
            
            info = ", ".join(info_parts)
            print(f"{indent}[{role}] {info}")
            
            # Recurse
            children = []
            try:
                children.extend(node.AXChildren or [])
            except: pass
            try:
                children.extend(node.AXSheets or [])
            except: pass
            
            for child in children:
                queue.append((child, depth + 1))
                
    print("\n=== END UI DUMP ===")

if __name__ == "__main__":
    dump_ui()
