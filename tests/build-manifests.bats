#!/usr/bin/env bats

setup() {
    TEST_DIR=$(mktemp -d)
    MANIFESTS_DIR="$TEST_DIR/manifests"
    APPS_DIR="$TEST_DIR/apps"
    BASE_PATH="$TEST_DIR/base"
    mkdir -p "$MANIFESTS_DIR" "$APPS_DIR" "$BASE_PATH"

    SCRIPT_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
    BUILD_SCRIPT="$SCRIPT_DIR/build-manifests.sh"

    # Source the script functions without running the main logic
    # We do this by extracting only the functions
    FUNC_FILE="$TEST_DIR/functions.sh"
    sed -n '/^# Function to extract version/,/^# Check if yq is available/p' "$BUILD_SCRIPT" | head -n -2 > "$FUNC_FILE"
}

teardown() {
    rm -rf "$TEST_DIR"
}

# --- Helper function tests using yq ---

@test "get_app_name extracts metadata.name" {
    cat > "$TEST_DIR/app.yaml" <<'EOF'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
spec:
  destination:
    namespace: default
EOF
    source "$TEST_DIR/functions.sh"
    result=$(get_app_name "$TEST_DIR/app.yaml")
    [ "$result" = "my-app" ]
}

@test "get_namespace extracts spec.destination.namespace" {
    cat > "$TEST_DIR/app.yaml" <<'EOF'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
spec:
  destination:
    namespace: monitoring
EOF
    source "$TEST_DIR/functions.sh"
    result=$(get_namespace "$TEST_DIR/app.yaml")
    [ "$result" = "monitoring" ]
}

@test "get_chart_name extracts chart from sources" {
    cat > "$TEST_DIR/app.yaml" <<'EOF'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
spec:
  sources:
    - repoURL: https://charts.example.com
      chart: nginx
      targetRevision: 1.0.0
EOF
    source "$TEST_DIR/functions.sh"
    result=$(get_chart_name "$TEST_DIR/app.yaml")
    [ "$result" = "nginx" ]
}

@test "get_chart_version extracts targetRevision from sources" {
    cat > "$TEST_DIR/app.yaml" <<'EOF'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
spec:
  sources:
    - repoURL: https://charts.example.com
      chart: nginx
      targetRevision: 2.3.1
EOF
    source "$TEST_DIR/functions.sh"
    result=$(get_chart_version "$TEST_DIR/app.yaml")
    [ "$result" = "2.3.1" ]
}

@test "get_chart_repo extracts repoURL from sources" {
    cat > "$TEST_DIR/app.yaml" <<'EOF'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
spec:
  sources:
    - repoURL: https://charts.example.com
      chart: nginx
      targetRevision: 1.0.0
EOF
    source "$TEST_DIR/functions.sh"
    result=$(get_chart_repo "$TEST_DIR/app.yaml")
    [ "$result" = "https://charts.example.com" ]
}

@test "get_release_name returns helm.releaseName when set" {
    cat > "$TEST_DIR/app.yaml" <<'EOF'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
spec:
  sources:
    - repoURL: https://charts.example.com
      chart: nginx
      targetRevision: 1.0.0
      helm:
        releaseName: custom-release
EOF
    source "$TEST_DIR/functions.sh"
    result=$(get_release_name "$TEST_DIR/app.yaml")
    [ "$result" = "custom-release" ]
}

@test "get_release_name falls back to app name when releaseName not set" {
    cat > "$TEST_DIR/app.yaml" <<'EOF'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
spec:
  sources:
    - repoURL: https://charts.example.com
      chart: nginx
      targetRevision: 1.0.0
EOF
    source "$TEST_DIR/functions.sh"
    result=$(get_release_name "$TEST_DIR/app.yaml")
    [ "$result" = "my-app" ]
}

@test "get_chart_name returns empty for path-based app" {
    cat > "$TEST_DIR/app.yaml" <<'EOF'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
spec:
  source:
    path: manifests/app1
    repoURL: https://github.com/example/repo
EOF
    source "$TEST_DIR/functions.sh"
    result=$(get_chart_name "$TEST_DIR/app.yaml")
    [ -z "$result" ]
}

@test "get_path_sources extracts path from spec.source" {
    mkdir -p "$TEST_DIR/base/manifests/app1"
    cat > "$TEST_DIR/app.yaml" <<'EOF'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
spec:
  source:
    path: manifests/app1
    repoURL: https://github.com/example/repo
EOF
    source "$TEST_DIR/functions.sh"
    BASE_PATH="$TEST_DIR/base"
    result=$(get_path_sources "$TEST_DIR/app.yaml")
    [[ "$result" == *"manifests/app1"* ]]
}

@test "get_path_sources extracts path from spec.sources[]" {
    mkdir -p "$TEST_DIR/base/manifests/app1"
    cat > "$TEST_DIR/app.yaml" <<'EOF'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
spec:
  sources:
    - path: manifests/app1
      repoURL: https://github.com/example/repo
EOF
    source "$TEST_DIR/functions.sh"
    BASE_PATH="$TEST_DIR/base"
    result=$(get_path_sources "$TEST_DIR/app.yaml")
    [[ "$result" == *"manifests/app1"* ]]
}

@test "is_kustomize_app returns true when kustomization.yaml exists" {
    mkdir -p "$TEST_DIR/base/k8s/app1"
    touch "$TEST_DIR/base/k8s/app1/kustomization.yaml"
    cat > "$TEST_DIR/app.yaml" <<'EOF'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: kustomize-app
spec:
  source:
    path: k8s/app1
    repoURL: https://github.com/example/repo
EOF
    source "$TEST_DIR/functions.sh"
    BASE_PATH="$TEST_DIR/base"
    is_kustomize_app "$TEST_DIR/app.yaml"
}

@test "is_kustomize_app returns false when no kustomization file" {
    mkdir -p "$TEST_DIR/base/k8s/app1"
    touch "$TEST_DIR/base/k8s/app1/deployment.yaml"
    cat > "$TEST_DIR/app.yaml" <<'EOF'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: plain-app
spec:
  source:
    path: k8s/app1
    repoURL: https://github.com/example/repo
EOF
    source "$TEST_DIR/functions.sh"
    BASE_PATH="$TEST_DIR/base"
    ! is_kustomize_app "$TEST_DIR/app.yaml"
}

# --- Full script integration tests ---

@test "processes path-based application and copies manifests" {
    mkdir -p "$BASE_PATH/k8s/app1"
    echo "kind: Deployment" > "$BASE_PATH/k8s/app1/deployment.yaml"
    echo "kind: Service" > "$BASE_PATH/k8s/app1/service.yaml"

    cat > "$APPS_DIR/app1.yaml" <<EOF
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-path-app
spec:
  source:
    path: k8s/app1
    repoURL: https://github.com/example/repo
  destination:
    namespace: default
EOF

    export HELM_KUBEVERSION="1.30.0"
    run bash "$BUILD_SCRIPT" "$MANIFESTS_DIR" "$APPS_DIR" "$BASE_PATH" ""

    [ "$status" -eq 0 ]
    [ -f "$MANIFESTS_DIR/my-path-app/deployment.yaml" ]
    [ -f "$MANIFESTS_DIR/my-path-app/service.yaml" ]
}

@test "skips files matching skip-files pattern" {
    mkdir -p "$BASE_PATH/k8s/app1"
    echo "kind: Deployment" > "$BASE_PATH/k8s/app1/deployment.yaml"

    cat > "$APPS_DIR/app1.yaml" <<EOF
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
spec:
  source:
    path: k8s/app1
    repoURL: https://github.com/example/repo
  destination:
    namespace: default
EOF
    cat > "$APPS_DIR/skip-me.yaml" <<EOF
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: skipped-app
spec:
  source:
    path: k8s/app1
    repoURL: https://github.com/example/repo
  destination:
    namespace: default
EOF

    export HELM_KUBEVERSION="1.30.0"
    run bash "$BUILD_SCRIPT" "$MANIFESTS_DIR" "$APPS_DIR" "$BASE_PATH" "skip-me.yaml"

    [ "$status" -eq 0 ]
    [ -d "$MANIFESTS_DIR/my-app" ]
    [ ! -d "$MANIFESTS_DIR/skipped-app" ]
}

@test "fails when yq is not available" {
    # Create a minimal PATH with essential tools but without yq
    local bin_dir="$TEST_DIR/bin"
    mkdir -p "$bin_dir"
    for cmd in bash echo grep cut find mkdir kubectl; do
        local p
        p=$(command -v "$cmd" 2>/dev/null) && ln -sf "$p" "$bin_dir/$cmd"
    done

    run env PATH="$bin_dir" bash "$BUILD_SCRIPT" "$MANIFESTS_DIR" "$APPS_DIR" "$BASE_PATH" ""

    [ "$status" -ne 0 ]
    [[ "$output" == *"yq is required"* ]]
}

@test "creates manifests directory if it does not exist" {
    rm -rf "$MANIFESTS_DIR"

    cat > "$APPS_DIR/app1.yaml" <<EOF
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: test-app
spec:
  source:
    path: nonexistent-path
    repoURL: https://github.com/example/repo
  destination:
    namespace: default
EOF

    export HELM_KUBEVERSION="1.30.0"
    run bash "$BUILD_SCRIPT" "$MANIFESTS_DIR" "$APPS_DIR" "$BASE_PATH" ""

    [ "$status" -eq 0 ]
    [ -d "$MANIFESTS_DIR" ]
}

@test "processes ApplicationSet with git directory generator" {
    # Create a unique directory structure that won't clash with filesystem globs
    local envs_dir="$TEST_DIR/appset_envs"
    mkdir -p "$envs_dir/dev"
    mkdir -p "$envs_dir/staging"
    echo "kind: ConfigMap" > "$envs_dir/dev/config.yaml"
    echo "kind: ConfigMap" > "$envs_dir/staging/config.yaml"

    # Use the absolute base path (without glob) as the generator path
    # with subdirectories to iterate
    cat > "$APPS_DIR/appset.yaml" <<EOF
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: my-appset
spec:
  generators:
    - git:
        directories:
          - path: $envs_dir
  template:
    metadata:
      name: env-{{path.basename}}
    spec:
      source:
        path: "{{path}}"
      destination:
        namespace: default
EOF

    export HELM_KUBEVERSION="1.30.0"
    run bash "$BUILD_SCRIPT" "$MANIFESTS_DIR" "$APPS_DIR" "$BASE_PATH" ""

    [ "$status" -eq 0 ]
    [ -d "$MANIFESTS_DIR/env-dev" ]
    [ -d "$MANIFESTS_DIR/env-staging" ]
    [ -f "$MANIFESTS_DIR/env-dev/config.yaml" ]
    [ -f "$MANIFESTS_DIR/env-staging/config.yaml" ]
}

@test "processes ApplicationSet with BASE_PATH relative paths" {
    # Create directory structure under BASE_PATH
    mkdir -p "$BASE_PATH/apps/bots/bot1"
    mkdir -p "$BASE_PATH/apps/bots/bot2"
    echo "kind: Deployment" > "$BASE_PATH/apps/bots/bot1/deployment.yaml"
    echo "kind: Service" > "$BASE_PATH/apps/bots/bot2/service.yaml"

    cat > "$APPS_DIR/appset.yaml" <<'EOF'
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: bots
  namespace: argocd
spec:
  generators:
    - git:
        repoURL: https://github.com/example/repo.git
        revision: main
        directories:
          - path: apps/bots/*
  template:
    metadata:
      name: "bots-{{path.basename}}"
    spec:
      source:
        repoURL: https://github.com/example/repo.git
        targetRevision: main
        path: "{{path}}"
      destination:
        namespace: bots
EOF

    export HELM_KUBEVERSION="1.30.0"
    run bash "$BUILD_SCRIPT" "$MANIFESTS_DIR" "$APPS_DIR" "$BASE_PATH" ""

    [ "$status" -eq 0 ]
    [ -d "$MANIFESTS_DIR/bots-bot1" ]
    [ -d "$MANIFESTS_DIR/bots-bot2" ]
    [ -f "$MANIFESTS_DIR/bots-bot1/deployment.yaml" ]
    [ -f "$MANIFESTS_DIR/bots-bot2/service.yaml" ]
}

@test "processes ApplicationSet with kustomize templates" {
    # Create kustomize directory structure
    local envs_dir="$TEST_DIR/appset_kustomize"
    mkdir -p "$envs_dir/app1"
    cat > "$envs_dir/app1/kustomization.yaml" <<'EOF'
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - deployment.yaml
EOF
    cat > "$envs_dir/app1/deployment.yaml" <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: test
spec:
  replicas: 1
  selector:
    matchLabels:
      app: test
  template:
    metadata:
      labels:
        app: test
    spec:
      containers:
        - name: test
          image: nginx
EOF

    cat > "$APPS_DIR/appset.yaml" <<EOF
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: kustomize-appset
spec:
  generators:
    - git:
        directories:
          - path: $envs_dir
  template:
    metadata:
      name: kust-{{path.basename}}
    spec:
      source:
        path: "{{path}}"
      destination:
        namespace: default
EOF

    export HELM_KUBEVERSION="1.30.0"
    run bash "$BUILD_SCRIPT" "$MANIFESTS_DIR" "$APPS_DIR" "$BASE_PATH" ""

    [ "$status" -eq 0 ]
    [ -d "$MANIFESTS_DIR/kust-app1" ]
    [ -f "$MANIFESTS_DIR/kust-app1/manifests.yaml" ]
}

@test "handles empty apps directory gracefully" {
    export HELM_KUBEVERSION="1.30.0"
    run bash "$BUILD_SCRIPT" "$MANIFESTS_DIR" "$APPS_DIR" "$BASE_PATH" ""

    [ "$status" -eq 0 ]
    [[ "$output" == *"All manifests generated successfully"* ]]
}

@test "get_values_files extracts $values references" {
    mkdir -p "$TEST_DIR/base/helm-values"
    echo "replicas: 3" > "$TEST_DIR/base/helm-values/values.yaml"

    cat > "$TEST_DIR/app.yaml" <<'EOF'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
spec:
  sources:
    - repoURL: https://github.com/example/repo
      path: helm-values
    - repoURL: https://charts.example.com
      chart: nginx
      targetRevision: 1.0.0
      helm:
        valueFiles:
          - $values/helm-values/values.yaml
EOF
    source "$TEST_DIR/functions.sh"
    BASE_PATH="$TEST_DIR/base"
    result=$(get_values_files "$TEST_DIR/app.yaml")
    [[ "$result" == *"--values"* ]]
    [[ "$result" == *"values.yaml"* ]]
}
