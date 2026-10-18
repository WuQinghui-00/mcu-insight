// Telemetry —— 运行期遥测数据（从 MCU-Insight 导出的 CSV 读入）
//
// CSV 格式（第一行是表头）：
//   metric,min,max
//   heap.min,185568,283908
//   task.format.stack_free_min,56,56
#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <ostream>
#include <string>
#include <vector>

#include "reporter.hpp"

namespace fwres {

struct MetricRange {
    std::string name;
    double min_value = 0.0;
    double max_value = 0.0;
};

class Telemetry {
public:
    explicit Telemetry(const std::string& csv_path);

    const std::string& path() const { return path_; }
    const std::vector<MetricRange>& metrics() const { return metrics_; }
    bool has(const std::string& name) const { return index_.count(name) != 0; }
    double min_of(const std::string& name) const;
    double max_of(const std::string& name) const;

private:
    std::string path_;
    std::vector<MetricRange> metrics_;
    std::map<std::string, std::size_t> index_;
};

// 输出"构建 vs 运行"对比。返回 false 表示有指标低于阈值。
bool write_runtime_section(std::ostream& out, const ReportData& d, const Telemetry& t,
                           std::uint64_t stack_warn = 256,
                           std::uint64_t heap_warn = 20 * 1024);

}  // namespace fwres