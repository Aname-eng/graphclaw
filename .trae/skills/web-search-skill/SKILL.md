---
name: "web-search-skill"
description: "Search web pages using Bing browser automation. Invoke when user asks for news, web info, or requires browsing multiple pages to gather information."
---

# Web Search Skill

This skill performs web searches using browser automation (CDP/Playwright).

## Hard Requirements

1. **Must use Bing Search** - Baidu is strictly prohibited
2. **Must click into article pages** - Cannot stop at search results page
3. **Must browse multiple articles** - Visit at least 20 different article pages
4. **Must extract article content** - Not just search summaries
5. **Must use current year 2026** for time-sensitive queries

## Workflow

1. Navigate to Bing search with query
2. Extract article links from search results
3. Visit each article page (up to 20)
4. Extract article body content using JavaScript
5. Compile all content into a report

## Tools Used

- `browser_navigate` - Open web pages
- `browser_execute_js` - Extract content
- `browser_close` - Clean up

## Example Usage

```python
# Search for AI news
search_queries = [
    "AI artificial intelligence latest news 2026",
    "artificial intelligence breakthrough 2026"
]

for query in search_queries:
    # Navigate to Bing
    result = browser_navigate(f"https://www.bing.com/search?q={query}")
    
    # Extract links
    links = browser_execute_js("""
        () => {
            const links = [];
            document.querySelectorAll('li.b_algo h2 a').forEach(a => {
                links.push({url: a.href, title: a.innerText});
            });
            return JSON.stringify(links.slice(0, 20));
        }
    """)
    
    # Visit each article and extract content
    for link in links:
        browser_navigate(link.url)
        content = browser_execute_js("""
            () => document.body.innerText.substring(0, 3000)
        """)
```
