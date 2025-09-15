# MCP Enhanced Chatbot

Intelligent chatbot with MCP servers for CSV analysis, system monitoring, file operations, and Git management.

## Installation

```bash
pip install anthropic aiohttp rich python-dotenv mcp pandas numpy matplotlib seaborn scikit-learn scipy
npm install -g npx
curl -LsSf https://astral.sh/uv/install.sh | sh  # For uvx (Git server)
```

## Configuration

Create `.env` file:
```env
ANTHROPIC_API_KEY=your_api_key_here
REMOTE_MCP_URL=https://your-remote-server.com  # Optional
```

## Usage

```bash
python chatbot.py
```

## Example Interactions

```
"Analyze the estudiantes.csv file"
"Show current system status" 
"List files in workspace"
"Show git status"
"Create a histogram of the price column"
```

## Requirements

- Python 3.8+
- Anthropic API key
- Node.js (for filesystem server)
- uvx (for Git server)
- CSV server from: https://github.com/SRV-JSPA/mcp_server.git

# Advanced CSV Analysis MCP Server

MCP server for comprehensive CSV data analysis, cleaning, and visualization.

## Installation

```bash
git clone https://github.com/SRV-JSPA/mcp_server.git
cd mcp_server
pip install pandas numpy matplotlib seaborn scikit-learn scipy
```

## Usage

```bash
python csv_mcp_server.py
```

## Tools

| Tool | Description | Required Parameters |
|------|-------------|-------------------|
| `analyze_csv` | Statistical analysis | `file_path` |
| `detect_outliers_in_csv` | Find outliers | `file_path`, `method` (iqr/zscore/dbscan) |
| `calculate_correlations_csv` | Correlation analysis | `file_path`, `method` (pearson/spearman) |
| `clean_csv_data` | Data cleaning | `file_path`, `operations` |
| `filter_csv_data` | Filter data | `file_path`, `filters` |
| `group_csv_data` | Group & aggregate | `file_path`, `group_by`, `aggregations` |
| `create_csv_visualization` | Create charts | `file_path`, `plot_type`, `columns` |
| `debug_workspace` | Debug info | None |

## Examples

**Basic analysis:**
```json
{"name": "analyze_csv", "arguments": {"file_path": "data.csv"}}
```

**Outlier detection:**
```json
{"name": "detect_outliers_in_csv", "arguments": {"file_path": "data.csv", "method": "iqr"}}
```

**Create histogram:**
```json
{"name": "create_csv_visualization", "arguments": {"file_path": "data.csv", "plot_type": "histogram", "columns": ["price"]}}
```

## Configuration

Set workspace directory:
```bash
export MCP_WORKSPACE_DIR=/path/to/workspace
```

## Requirements

- Python 3.8+
- CSV files in workspace directory
- Compatible MCP client