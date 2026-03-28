#!/bin/bash

# 进入项目根目录（与 docker-compose.yml、app.py 同级）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.." || exit 1

# AI Assistant Docker 启动脚本
# 支持多种操作：启动、停止、重启、查看日志等

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 函数：打印提示信息
print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# 检查 Docker 是否安装
check_docker() {
    if ! command -v docker &> /dev/null; then
        print_error "Docker 未安装，请先安装 Docker"
        exit 1
    fi

    if ! command -v docker-compose &> /dev/null; then
        print_error "Docker Compose 未安装，请先安装 Docker Compose"
        exit 1
    fi

    print_success "Docker 环境检查完成"
}

# 检查 .env 文件
check_env() {
    if [ ! -f ".env" ]; then
        print_error ".env 文件不存在"
        exit 1
    fi

    if ! grep -q "DIFY_API_KEY" .env; then
        print_error ".env 文件中缺少 DIFY_API_KEY"
        exit 1
    fi

    print_success ".env 文件检查完成"
}

# 启动服务
start_services() {
    print_info "开始启动 AI Assistant..."
    check_docker
    check_env

    print_info "构建镜像并启动容器..."
    docker-compose up -d

    print_info "等待服务启动..."
    sleep 5

    # 检查服务状态
    if docker-compose ps | grep -q "ai-assistant.*Up"; then
        print_success "AI Assistant 后端已启动"
    else
        print_error "AI Assistant 后端启动失败"
        docker-compose logs ai-assistant
        exit 1
    fi

    if docker-compose ps | grep -q "nginx.*Up"; then
        print_success "Nginx 反向代理已启动"
    else
        print_warning "Nginx 启动失败（可选）"
    fi

    echo ""
    print_success "服务启动完成！"
    echo ""
    echo "📍 访问地址："
    echo "   经 Nginx: http://localhost:8080"
    echo "   后端 API: http://localhost:8000"
    echo "   健康检查: http://localhost:8000/api/health"
    echo ""
    print_info "查看日志: docker-compose logs -f ai-assistant"
}

# 停止服务
stop_services() {
    print_info "停止 AI Assistant 服务..."
    docker-compose down
    print_success "服务已停止"
}

# 重启服务
restart_services() {
    print_info "重启 AI Assistant 服务..."
    docker-compose restart
    sleep 3
    print_success "服务已重启"
}

# 查看日志
view_logs() {
    if [ "$1" == "backend" ]; then
        docker-compose logs -f ai-assistant
    elif [ "$1" == "nginx" ]; then
        docker-compose logs -f nginx
    else
        docker-compose logs -f
    fi
}

# 查看服务状态
status_services() {
    print_info "获取服务状态..."
    docker-compose ps
    echo ""
    print_info "容器统计信息："
    docker stats --no-stream
}

# 进入容器 Shell
enter_shell() {
    if [ "$1" == "backend" ]; then
        docker-compose exec ai-assistant bash
    elif [ "$1" == "nginx" ]; then
        docker-compose exec nginx sh
    else
        print_error "请指定容器: backend 或 nginx"
        exit 1
    fi
}

# 清理资源
cleanup() {
    print_info "清理 Docker 资源..."
    docker-compose down -v
    docker container prune -f
    docker image prune -f
    print_success "清理完成"
}

# 健康检查
health_check() {
    print_info "执行健康检查..."

    # 检查后端
    if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
        print_success "后端服务健康 ✓"
    else
        print_error "后端服务异常"
    fi

    # 检查 Nginx 代理
    if curl -sf http://localhost:8080/api/health > /dev/null 2>&1; then
        print_success "Nginx 代理健康 ✓"
    else
        print_warning "Nginx 代理异常（可检查 8080 是否映射）"
    fi
}

# 显示帮助信息
show_help() {
    cat << EOF
🐳 AI Assistant Docker 管理脚本

用法: ./scripts/docker-start.sh [命令] [选项]

命令:
    start           启动所有服务
    stop            停止所有服务
    restart         重启所有服务
    logs [backend|nginx]
                    查看服务日志（默认查看所有）
    status          查看服务状态
    shell [backend|nginx]
                    进入容器 Shell
    health          执行健康检查
    clean           清理 Docker 资源（谨慎使用）
    help            显示此帮助信息

示例:
    ./scripts/docker-start.sh start
    ./scripts/docker-start.sh logs backend
    ./scripts/docker-start.sh shell backend
    ./scripts/docker-start.sh status

EOF
}

# 主函数
main() {
    case "${1:-start}" in
        start)
            start_services
            ;;
        stop)
            stop_services
            ;;
        restart)
            restart_services
            ;;
        logs)
            view_logs "$2"
            ;;
        status)
            status_services
            ;;
        shell)
            enter_shell "$2"
            ;;
        health)
            health_check
            ;;
        clean)
            cleanup
            ;;
        help|-h|--help)
            show_help
            ;;
        *)
            print_error "未知命令: $1"
            echo ""
            show_help
            exit 1
            ;;
    esac
}

main "$@"

