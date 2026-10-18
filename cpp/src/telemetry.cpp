#include "telemetry.hpp"

#include <fstream>
#include <stdexcept>
#include <string>

namespace fwres {

Telemetry::Telemetry(const std::string& csv_path) : path_(csv_path) {
    std::ifstream in(csv_path);
    if (!in) {
        throw std::runtime_error("无法打开遥测 CSV: " + csv_path);
    }

    std::string line;
    bool first = true;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        if (first) {
            first = false;
            if (line.find("metric") != std::string::npos) continue;   // 表头
        }
        const std::size_t p1 = line.find(',');
        if (p1 == std::string::npos) continue;
        const std::size_t p2 = line.find(',', p1 + 1);

        MetricRange r;
        r.name = line.substr(0, p1);
        try {
            const std::size_t len = (p2 == std::string::npos) ? std::string::npos : (p2 - p1 - 1);
            r.min_value = std::stod(line.substr(p1 + 1, len));
            r.max_value = (p2 == std::string::npos) ? r.min_value : std::stod(line.substr(p2 + 1));
        } catch (const std::exception&) {
            continue;
        }
        index_[r.name] = metrics_.size();
        metrics_.push_back(r);
    }
}

double Telemetry::min_of(const std::string& name) const {
    const auto it = index_.find(name);
    return it == index_.end() ? 0.0 : metrics_[it->second].min_value;
}

double Telemetry::max_of(const std::string& name) const {
    const auto it = index_.find(name);
    return it == index_.end() ? 0.0 : metrics_[it->second].max_value;
}

bool write_runtime_section(std::ostream& out, const ReportData& d, const Telemetry& t,
                           std::uint64_t stack_warn, std::uint64_t heap_warn) {
    out << "\n=== 构建 vs 运行 ===\n";
    out << "静态 DRAM 占用(构建期) : " << commas(d.bytes[static_cast<std::size_t>(Region::Dram)]) << " B\n";

    bool ok = true;

    if (t.has("heap.min")) {
        const std::uint64_t heap_min = static_cast<std::uint64_t>(t.min_of("heap.min"));
        out << "运行期最小堆余量       : " << commas(heap_min) << " B";
        if (heap_min < heap_warn) { out << "   风险 (<" << commas(heap_warn) << " B)"; ok = false; }
        out << "\n";
    }
    if (t.has("heap.largest")) {
        out << "运行期最大可分配块     : " << commas(static_cast<std::uint64_t>(t.min_of("heap.largest"))) << " B\n";
    }

    out << "\n任务栈余量（取运行期最小值，越小越危险）：\n";
    const std::string suffix = ".stack_free_min";
    bool any_task = false;
    for (const auto& m : t.metrics()) {
        if (m.name.rfind("task.", 0) != 0) continue;
        if (m.name.size() <= 5 + suffix.size()) continue;
        if (m.name.compare(m.name.size() - suffix.size(), suffix.size(), suffix) != 0) continue;

        const std::string task = m.name.substr(5, m.name.size() - 5 - suffix.size());
        const std::uint64_t free_bytes = static_cast<std::uint64_t>(m.min_value);
        const bool risk = free_bytes < stack_warn;
        out << "  " << pad_display(task, 16) << pad_display(commas(free_bytes) + " B", 12);
        out << (risk ? "风险 (<" + commas(stack_warn) + " B)" : "正常") << "\n";
        if (risk) ok = false;
        any_task = true;
    }
    if (!any_task) {
        out << "  (遥测里没有 task.*.stack_free_min 指标)\n";
    }

    out << "\n结果: " << (ok ? "全部指标在阈值内" : "存在低于阈值的指标，退出码 4") << "\n";
    return ok;
}

}  // namespace fwres