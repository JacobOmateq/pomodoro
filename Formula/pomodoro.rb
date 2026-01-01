class Pomodoro < Formula
  desc "Terminal-based Pomodoro timer with statistics and CalDAV sync"
  homepage "https://github.com/JacobOmateq/pomodoro"
  url "https://github.com/JacobOmateq/pomodoro/archive/refs/heads/main.zip"
  version "1.0.0"
  sha256 "a6fafa09aea82bb48f826d820c8694e044420e135f4e2004eb900435d0d0fc76" # This will be calculated when you create a release
  
  depends_on "python@3.11"

  def install
    python3 = Formula["python@3.11"].opt_bin/"python3.11"
    
    # Install dependencies from requirements.txt
    requirements = buildpath/"requirements.txt"
    system python3, "-m", "pip", "install", "--prefix=#{libexec}", "-r", requirements
    
    # Copy script and modify shebang
    libexec.install "pomodoro.py"
    inreplace libexec/"pomodoro.py", "#!/usr/bin/env python3", "#!#{python3}"
    
    # Create wrapper script
    (bin/"pomodoro").write <<~EOS
      #!/bin/bash
      export PYTHONPATH="#{libexec}/lib/python3.11/site-packages:$PYTHONPATH"
      exec #{python3} "#{libexec}/pomodoro.py" "$@"
    EOS
    chmod 0755, bin/"pomodoro"
  end

  test do
    system "#{bin}/pomodoro", "--help"
  end
end
